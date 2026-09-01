"""Deterministic candidate funnel: features, quant/evidence/focus, clusters.

Everything here is pure and deterministic.  It never calls a model, never
reads free-text snippets, never trusts caller-supplied scores, and never lets
dict/thread/DB completion order decide a result.  The approved formulas come
exclusively from the ADR-039 manifests in ``screening.manifests``; adding,
deleting, or re-weighting factors is a manifest violation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Any, Final, cast

from seven_lens.clock.market_clock import MarketDayKind, MarketSession
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import (
    SplitAdjustment,
    daily_bars_from_record,
    validate_nyse_session_window,
)
from seven_lens.screening.contracts import (
    EVIDENCE_CAP,
    QUANT_CAP,
    CandidateEntry,
    CandidateStage,
    FactorStatus,
    FeatureVector,
    RawFeature,
    SectorAssignment,
    _expected_focus_from_evidence,
    _finalize_candidate_entry,
    _finalize_feature_vector,
)
from seven_lens.screening.manifests import (
    ClusterManifest,
    ClusterStatus,
    FactorManifest,
    FundamentalConcept,
    SicDivision,
    cluster_manifest,
    factor_manifest,
    sector_manifest,
)
from seven_lens.securities.contracts import (
    SecurityId,
    SecurityIdentityRecord,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
)
from seven_lens.securities.quarantine import QuarantineDecision, QuarantineOutcome
from seven_lens.sources.adapters.records import NormalizedSourceRecord
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.contracts import UniverseSnapshot

_FORMULA_VERSION: Final = "p4-factor-v1.0"
_SCHEMA_VERSION: Final = SchemaVersion("1.0.0")
_CLUSTER_ID_DOMAIN: Final = b"seven-lens.p4c.cluster-id.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_FACTOR_SESSION_WINDOW: Final = 252
_CLUSTER_SESSION_WINDOW: Final = 126
_SCREENING_DECIMAL_CONTEXT: Final = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
)
MAX_CLUSTER_RESULT_BYTES: Final = 1_048_576
MAX_CLUSTER_MEMBERS: Final = 256
MAX_CLUSTER_SOURCE_REFS: Final = 1_024
_EVIDENCE_SOURCE_FAMILIES: Final = frozenset(
    {
        P4SourceFamily.ALPACA_ASSETS,
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        P4SourceFamily.SEC_EDGAR,
        P4SourceFamily.ISSUER_IR,
        P4SourceFamily.EXCHANGE_OFFICIAL,
    }
)


@dataclass(frozen=True, slots=True)
class _ClusterAuthority:
    """Private content-bound capability for cluster finalization/readback."""

    cluster_id: str


@dataclass(frozen=True, slots=True)
class _RecordAuthority:
    """Private capability bound to one normalized screening input value."""

    fingerprint: tuple[object, ...]
    source_record_hash: str | None = None
    identity_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _EvidenceAuthority:
    """Private capability bound to typed evidence-gate metadata."""

    fingerprint: tuple[object, ...]


def _canonical_cluster_wire_bytes(wire: dict[str, object]) -> bytes:
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(canonical) > MAX_CLUSTER_RESULT_BYTES:
        raise ValueError(
            f"cluster result exceeds the {MAX_CLUSTER_RESULT_BYTES}-byte serialized cap"
        )
    return canonical


def _session_calendar(sessions: tuple[MarketSession, ...]) -> dict[TradingDate, MarketSession]:
    """Validate and index the explicit market-session authority."""
    if type(sessions) is not tuple or any(
        type(session) is not MarketSession for session in sessions
    ):
        raise ValueError("sessions must be a tuple of MarketSession values")
    dates = [session.trading_date for session in sessions]
    if len(dates) != len(set(dates)):
        raise ValueError("sessions must not contain duplicate trading dates")
    if dates != sorted(dates, key=lambda date: date.value):
        raise ValueError("sessions must be ordered by trading date")
    if any(
        session.trading_date.value.weekday() >= 5 and session.day_kind is not MarketDayKind.CLOSED
        for session in sessions
    ):
        raise ValueError("NYSE weekends must be explicit CLOSED sessions")
    for session in sessions:
        validate_nyse_session_window(session)
    return {session.trading_date: session for session in sessions}


def _latest_open_session_dates(
    session_by_date: Mapping[TradingDate, MarketSession],
    *,
    cutoff_date: date,
    count: int,
) -> tuple[TradingDate, ...]:
    """Return the latest explicit qualifying sessions before a cutoff."""
    if type(cutoff_date) is not date:
        raise ValueError("cutoff_date requires a date")
    dates = sorted(
        (
            date
            for date, session in session_by_date.items()
            if date.value < cutoff_date
            and session.day_kind in (MarketDayKind.REGULAR, MarketDayKind.HALF_DAY)
        ),
        key=lambda date: date.value,
    )
    selected = tuple(dates[-count:])
    if len(selected) == count:
        present = {trading_date.value for trading_date in session_by_date}
        current = selected[0].value
        while current < cutoff_date:
            if current.weekday() < 5 and current not in present:
                raise ValueError("NYSE calendar window must include every weekday explicitly")
            current += timedelta(days=1)
    return selected


def _has_latest_session_coverage(
    dates: Sequence[TradingDate],
    session_by_date: Mapping[TradingDate, MarketSession],
    *,
    cutoff_date: date,
    count: int,
) -> bool:
    """Require the latest explicit qualifying session set, not older backfill."""
    expected = _latest_open_session_dates(
        session_by_date,
        cutoff_date=cutoff_date,
        count=count,
    )
    return len(expected) == count and len(dates) >= count and tuple(dates[-count:]) == expected


@dataclass(frozen=True, slots=True)
class SessionClose:
    """One completed NYSE regular-session close, ascending by session.

    ``available_at`` is part of the value, rather than an out-of-band
    assertion, so a late price correction cannot be replayed into an older
    factor or cluster cutoff.
    """

    trading_date: TradingDate
    close: Decimal
    source_ref: SourceRef
    available_at: UtcTimestamp
    security_id: SecurityId | None = None
    _authority: _RecordAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _RecordAuthority:
            raise ValueError("session closes must be finalized by the market-data authority")
        if type(self.trading_date) is not TradingDate:
            raise ValueError("trading_date requires an exact TradingDate")
        if type(self.close) is not Decimal or not self.close.is_finite() or self.close <= 0:
            raise ValueError("close must be a positive finite Decimal")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if self.source_ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS:
            raise ValueError("session closes require the historical-bars authority")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId or None")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        authority = self._authority
        assert type(authority) is _RecordAuthority
        if authority.fingerprint != _session_close_fingerprint(self):
            raise ValueError("session-close authority is not bound to frozen content")


@dataclass(frozen=True, slots=True)
class QuarterlyFact:
    """One normalized SEC fact used to assemble TTM fundamentals.

    ``fiscal_period`` is one of ``Q1``..``Q4`` for single-quarter facts, or
    ``YTD`` for year-to-date facts.  A YTD fact must also carry its explicit
    SEC fiscal-quarter label in ``fiscal_quarter``; calendar months are not a
    valid proxy because many issuers have non-calendar fiscal years.  When YTD
    facts are present, ``assemble_ttm`` computes single-quarter values by subtracting
    the prior YTD (YTD(Q1) - 0, YTD(Q2) - YTD(Q1), etc.) provided all four
    YTD facts exist with the same entity/currency/consolidation/fiscal year
    lineage.  This is the only allowed YTD derivation; the factor layer
    never applies ``abs()`` to correct an unknown sign.
    """

    concept: FundamentalConcept
    value: Decimal
    period_end: TradingDate
    fiscal_year: int
    fiscal_period: str
    currency: str
    entity: str
    consolidation: str
    source_ref: SourceRef
    available_at: UtcTimestamp
    security_id: SecurityId | None = None
    fiscal_quarter: str | None = None
    _authority: _RecordAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _RecordAuthority:
            raise ValueError("quarterly facts must be finalized by the SEC authority")
        if type(self.concept) is not FundamentalConcept:
            raise ValueError("concept requires an exact FundamentalConcept")
        if type(self.value) is not Decimal or not self.value.is_finite():
            raise ValueError("value must be a finite Decimal")
        if type(self.period_end) is not TradingDate:
            raise ValueError("period_end requires an exact TradingDate")
        if type(self.fiscal_year) is not int or not 1900 <= self.fiscal_year <= 2999:
            raise ValueError("fiscal_year must be a bounded integer")
        if type(self.fiscal_period) is not str or self.fiscal_period not in (
            "Q1",
            "Q2",
            "Q3",
            "Q4",
            "YTD",
        ):
            raise ValueError("fiscal_period must be one of Q1..Q4 or YTD")
        if self.fiscal_period == "YTD":
            if type(self.fiscal_quarter) is not str or self.fiscal_quarter not in (
                "Q1",
                "Q2",
                "Q3",
                "Q4",
            ):
                raise ValueError("YTD facts require an explicit fiscal_quarter Q1..Q4")
        elif self.fiscal_quarter is not None and self.fiscal_quarter != self.fiscal_period:
            raise ValueError("single-quarter fiscal_quarter must match fiscal_period")
        if type(self.currency) is not str or not self.currency:
            raise ValueError("currency requires non-empty bounded text")
        if type(self.entity) is not str or not self.entity:
            raise ValueError("entity requires non-empty bounded text")
        if type(self.consolidation) is not str or not self.consolidation:
            raise ValueError("consolidation requires non-empty bounded text")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if self.source_ref.family is not P4SourceFamily.SEC_EDGAR:
            raise ValueError("fundamental facts require the SEC EDGAR authority")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId or None")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        authority = self._authority
        assert type(authority) is _RecordAuthority
        if authority.fingerprint != _quarterly_fact_fingerprint(self):
            raise ValueError("quarterly-fact authority is not bound to frozen content")


@dataclass(frozen=True, slots=True)
class SharesObservation:
    """One point-in-time SEC shares-outstanding observation.

    Shares are not an unqualified scalar: the observation must retain its SEC
    lineage and availability instant so a future filing cannot enter a past
    market-cap calculation.
    """

    value: Decimal
    entity: str
    currency: str
    consolidation: str
    source_ref: SourceRef
    available_at: UtcTimestamp
    security_id: SecurityId | None = None
    _authority: _RecordAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _RecordAuthority:
            raise ValueError("shares observations must be finalized by the SEC authority")
        if type(self.value) is not Decimal or not self.value.is_finite() or self.value <= 0:
            raise ValueError("shares value must be a positive finite Decimal")
        for name in ("entity", "currency", "consolidation"):
            value = getattr(self, name)
            if type(value) is not str or not value:
                raise ValueError(f"shares {name} requires non-empty bounded text")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("shares source_ref requires an exact SourceRef")
        if self.source_ref.family is not P4SourceFamily.SEC_EDGAR:
            raise ValueError("shares require the SEC EDGAR authority")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("shares available_at requires canonical UTC")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId or None")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        authority = self._authority
        assert type(authority) is _RecordAuthority
        if authority.fingerprint != _shares_observation_fingerprint(self):
            raise ValueError("shares-observation authority is not bound to frozen content")


def _session_close_fingerprint(value: SessionClose) -> tuple[object, ...]:
    return (
        value.trading_date,
        value.close,
        value.source_ref,
        value.available_at,
        value.security_id,
    )


def _quarterly_fact_fingerprint(value: QuarterlyFact) -> tuple[object, ...]:
    return (
        value.concept,
        value.value,
        value.period_end,
        value.fiscal_year,
        value.fiscal_period,
        value.currency,
        value.entity,
        value.consolidation,
        value.source_ref,
        value.available_at,
        value.security_id,
        value.fiscal_quarter,
    )


def _shares_observation_fingerprint(value: SharesObservation) -> tuple[object, ...]:
    return (
        value.value,
        value.entity,
        value.currency,
        value.consolidation,
        value.source_ref,
        value.available_at,
        value.security_id,
    )


def _trusted_record(
    cls: type[Any],
    fingerprint: Callable[[Any], tuple[object, ...]],
    defaults: Mapping[str, object],
    values: Mapping[str, object],
) -> Any:
    body = dict(values)
    body.pop("_authority", None)
    for name, value in defaults.items():
        body.setdefault(name, value)
    provisional = object.__new__(cls)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    body["_authority"] = _RecordAuthority(fingerprint(provisional))
    return cls(**body)


def _finalize_session_close(**values: object) -> SessionClose:
    """Finalize a close from the trusted normalized market-data assembler."""
    return cast(
        SessionClose,
        _trusted_record(SessionClose, _session_close_fingerprint, {"security_id": None}, values),
    )


def _reconstruct_session_close(**values: object) -> SessionClose:
    """Reconstruct a close after DB/source-record payload validation."""
    return _finalize_session_close(**values)


def _finalize_quarterly_fact(**values: object) -> QuarterlyFact:
    """Finalize a fact from the trusted normalized SEC assembler."""
    return cast(
        QuarterlyFact,
        _trusted_record(
            QuarterlyFact,
            _quarterly_fact_fingerprint,
            {"security_id": None, "fiscal_quarter": None},
            values,
        ),
    )


def _reconstruct_quarterly_fact(**values: object) -> QuarterlyFact:
    """Reconstruct a fact after DB/source-record payload validation."""
    return _finalize_quarterly_fact(**values)


def _finalize_shares_observation(**values: object) -> SharesObservation:
    """Finalize shares from the trusted normalized SEC assembler."""
    return cast(
        SharesObservation,
        _trusted_record(
            SharesObservation,
            _shares_observation_fingerprint,
            {"security_id": None},
            values,
        ),
    )


def _reconstruct_shares_observation(**values: object) -> SharesObservation:
    """Reconstruct shares after DB/source-record payload validation."""
    return _finalize_shares_observation(**values)


def _source_bound_record(
    cls: type[Any],
    fingerprint: Callable[[Any], tuple[object, ...]],
    values: Mapping[str, object],
    *,
    source_record_hash: str,
    identity_hash: str,
) -> Any:
    """Finalize one projection while retaining its exact P4-A/P4-B authority."""
    body = dict(values)
    provisional = object.__new__(cls)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    body["_authority"] = _RecordAuthority(
        fingerprint(provisional),
        source_record_hash=source_record_hash,
        identity_hash=identity_hash,
    )
    return cls(**body)


def _adjusted_close_value(
    close: Decimal,
    trading_date: TradingDate,
    split_adjustments: tuple[SplitAdjustment, ...],
) -> Decimal:
    """Apply the existing confirmed, visible split adjustment arithmetic."""
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        adjusted = close
        for split in split_adjustments:
            if trading_date.value < split.ex_date.value:
                adjusted = adjusted * Decimal(split.denominator) / Decimal(split.numerator)
        return adjusted


def session_closes_from_record(
    record: NormalizedSourceRecord,
    *,
    security_id: SecurityId,
    identities: tuple[SecurityIdentityRecord, ...],
    known_at: UtcTimestamp,
    split_adjustments: tuple[SplitAdjustment, ...] = (),
) -> tuple[SessionClose, ...]:
    """Project factor closes from exact delayed-SIP daily-bar authority."""
    bars = daily_bars_from_record(
        record,
        security_id=security_id,
        identities=identities,
        known_at=known_at,
    )
    if type(split_adjustments) is not tuple or any(
        type(split) is not SplitAdjustment for split in split_adjustments
    ):
        raise ValueError("split_adjustments must be a tuple of SplitAdjustment values")
    for split in split_adjustments:
        split._verify_source_binding()
    if any(split.security_id != security_id for split in split_adjustments):
        raise ValueError("split adjustments must bind to the target security")
    bar_identity_hashes = {getattr(bar._authority, "identity_hash", None) for bar in bars}
    if split_adjustments and any(
        split.security_identity_hash not in bar_identity_hashes for split in split_adjustments
    ):
        raise ValueError("split adjustment does not bind to the historical-bar identity")
    latest_bar_date = max((bar.trading_date.value for bar in bars), default=None)
    visible_splits = tuple(
        split
        for split in split_adjustments
        if split.available_at.value <= known_at.value
        and (latest_bar_date is None or split.ex_date.value <= latest_bar_date)
    )
    closes: list[SessionClose] = []
    for bar in bars:
        authority = bar._authority
        if authority is None:
            raise ValueError("daily bar is missing its source authority")
        values: dict[str, object] = {
            "trading_date": bar.trading_date,
            "close": _adjusted_close_value(bar.close, bar.trading_date, visible_splits),
            "source_ref": bar.source_ref,
            "available_at": bar.available_at,
            "security_id": bar.security_id,
        }
        closes.append(
            cast(
                SessionClose,
                _source_bound_record(
                    SessionClose,
                    _session_close_fingerprint,
                    values,
                    source_record_hash=authority.source_record_hash,
                    identity_hash=authority.identity_hash,
                ),
            )
        )
    return tuple(closes)


def sec_factor_inputs_from_records(
    records: tuple[NormalizedSourceRecord, ...],
    *,
    identity: SecurityIdentityRecord,
    known_at: UtcTimestamp,
) -> tuple[tuple[QuarterlyFact, ...], SharesObservation]:
    """Project exact SEC Company Facts into factor facts and shares.

    Framed duration facts are single-quarter observations.  Unframed duration
    facts are accepted as YTD only in a complete Q1/Q2/Q3/FY cohort sharing
    one issuer, currency, fiscal year, consolidation scope, and fiscal start.
    This prevents calendar-month guesses for non-calendar fiscal issuers.
    """
    if (
        type(records) is not tuple
        or not records
        or any(type(record) is not NormalizedSourceRecord for record in records)
    ):
        raise ValueError("SEC factor records require a non-empty exact tuple")
    if type(identity) is not SecurityIdentityRecord or identity.cik is None:
        raise ValueError("SEC factor records require an exact CIK-bound identity")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    identity.verify_integrity()
    if not identity.known_at(known_at) or identity.status is not SecurityStatus.ACTIVE:
        raise ValueError("SEC factor identity is not active and visible at known_at")

    parsed: list[tuple[NormalizedSourceRecord, dict[str, object]]] = []
    for record in records:
        record.verify_integrity()
        if record.family is not P4SourceFamily.SEC_EDGAR or record.endpoint_id != "companyfacts":
            raise ValueError("factor fundamentals require SEC Company Facts records")
        if record.available_at is None or record.available_at.value > known_at.value:
            raise ValueError("SEC Company Fact is not available by known_at")
        payload = cast(dict[str, object], record.payload.to_dict())
        required = {
            "cik_padded",
            "taxonomy",
            "concept",
            "unit",
            "value",
            "start",
            "end",
            "fiscal_year",
            "fiscal_period",
            "form",
            "accession",
            "filed",
            "frame",
            "consolidation_scope",
        }
        allowed = required | {"sign_convention"}
        if not required <= set(payload) or not set(payload) <= allowed:
            raise ValueError("SEC Company Fact payload has an unexpected shape")
        if (
            payload.get("cik_padded") != identity.cik.value
            or payload.get("consolidation_scope") != "entire_filing_entity"
        ):
            raise ValueError("SEC Company Fact does not bind to the identity and entity scope")
        fiscal_year = payload.get("fiscal_year")
        if type(fiscal_year) is not int or not 1900 <= fiscal_year <= 2999:
            raise ValueError("SEC Company Fact fiscal year is invalid")
        parsed.append((record, payload))

    shares_candidates: list[tuple[TradingDate, SharesObservation, NormalizedSourceRecord]] = []
    facts: list[QuarterlyFact] = []
    unframed: dict[
        tuple[str, str, str, int, str],
        list[tuple[NormalizedSourceRecord, dict[str, object]]],
    ] = {}

    def _concept(payload: Mapping[str, object]) -> FundamentalConcept:
        try:
            return FundamentalConcept(f"{payload['taxonomy']}:{payload['concept']}")
        except (KeyError, ValueError) as error:
            raise ValueError("SEC Company Fact concept is not approved") from error

    def _fact_values(
        record: NormalizedSourceRecord,
        payload: Mapping[str, object],
        *,
        fiscal_period: str,
        fiscal_quarter: str,
    ) -> QuarterlyFact:
        values: dict[str, object] = {
            "concept": _concept(payload),
            "value": Decimal(str(payload["value"])),
            "period_end": TradingDate.from_isoformat(str(payload["end"])),
            "fiscal_year": payload["fiscal_year"],
            "fiscal_period": fiscal_period,
            "currency": str(payload["unit"]),
            "entity": str(payload["cik_padded"]),
            "consolidation": str(payload["consolidation_scope"]),
            "source_ref": SourceRef(record.record_id, record.family, record.record_hash),
            "available_at": record.available_at,
            "security_id": identity.security_id,
            "fiscal_quarter": fiscal_quarter,
        }
        return cast(
            QuarterlyFact,
            _source_bound_record(
                QuarterlyFact,
                _quarterly_fact_fingerprint,
                values,
                source_record_hash=record.record_hash,
                identity_hash=identity.identity_hash,
            ),
        )

    for record, payload in parsed:
        concept = _concept(payload)
        fiscal_period = payload.get("fiscal_period")
        if type(fiscal_period) is not str or fiscal_period not in {
            "Q1",
            "Q2",
            "Q3",
            "Q4",
            "FY",
        }:
            raise ValueError("SEC fiscal period is outside the closed factor set")
        if concept is FundamentalConcept.SHARES_OUTSTANDING:
            if payload.get("start") is not None or payload.get("unit") != "shares":
                raise ValueError("shares outstanding requires an instant shares fact")
            values = {
                "value": Decimal(str(payload["value"])),
                "entity": str(payload["cik_padded"]),
                "currency": "shares",
                "consolidation": str(payload["consolidation_scope"]),
                "source_ref": SourceRef(record.record_id, record.family, record.record_hash),
                "available_at": record.available_at,
                "security_id": identity.security_id,
            }
            shares_candidates.append(
                (
                    TradingDate.from_isoformat(str(payload["end"])),
                    cast(
                        SharesObservation,
                        _source_bound_record(
                            SharesObservation,
                            _shares_observation_fingerprint,
                            values,
                            source_record_hash=record.record_hash,
                            identity_hash=identity.identity_hash,
                        ),
                    ),
                    record,
                )
            )
            continue
        if payload.get("unit") != "USD":
            raise ValueError("fundamental factor records require the pinned USD unit")
        if concept is FundamentalConcept.CAPEX_PPE and (
            Decimal(str(payload["value"])) < 0
            or payload.get("sign_convention") != "positive_cash_outflow_provider_value"
        ):
            raise ValueError("CapEx requires the pinned positive-outflow convention")
        if concept is FundamentalConcept.ASSETS:
            if payload.get("start") is not None:
                raise ValueError("Assets requires an instant SEC fact")
            quarter = "Q4" if fiscal_period == "FY" else fiscal_period
            facts.append(
                _fact_values(
                    record,
                    payload,
                    fiscal_period=quarter,
                    fiscal_quarter=quarter,
                )
            )
            continue
        if payload.get("start") is None:
            raise ValueError("flow fundamentals require an explicit fiscal start")
        if payload.get("frame") is not None:
            if fiscal_period == "FY":
                raise ValueError("annual flow facts cannot be represented as one quarter")
            facts.append(
                _fact_values(
                    record,
                    payload,
                    fiscal_period=fiscal_period,
                    fiscal_quarter=fiscal_period,
                )
            )
            continue
        group_key = (
            concept.value,
            str(payload["unit"]),
            str(payload["cik_padded"]),
            cast(int, payload["fiscal_year"]),
            str(payload["start"]),
        )
        unframed.setdefault(group_key, []).append((record, payload))

    for group in unframed.values():
        by_quarter: dict[str, tuple[NormalizedSourceRecord, dict[str, object]]] = {}
        for item in group:
            label = "Q4" if item[1]["fiscal_period"] == "FY" else str(item[1]["fiscal_period"])
            if label in by_quarter:
                raise ValueError("unframed YTD cohort repeats a fiscal quarter")
            by_quarter[label] = item
        if set(by_quarter) != {"Q1", "Q2", "Q3", "Q4"}:
            raise ValueError("unframed flow facts require a complete Q1-Q4 YTD cohort")
        ends = [
            TradingDate.from_isoformat(str(by_quarter[label][1]["end"]))
            for label in ("Q1", "Q2", "Q3", "Q4")
        ]
        if [value.value for value in ends] != sorted(value.value for value in ends):
            raise ValueError("unframed YTD cohort period ends must strictly increase")
        for label in ("Q1", "Q2", "Q3", "Q4"):
            record, payload = by_quarter[label]
            facts.append(
                _fact_values(
                    record,
                    payload,
                    fiscal_period="YTD",
                    fiscal_quarter=label,
                )
            )

    if not shares_candidates:
        raise ValueError("SEC factor records require shares outstanding")
    shares_candidates.sort(key=lambda item: (item[0].value, item[1].available_at.value))
    latest_date = shares_candidates[-1][0]
    latest_period = [item for item in shares_candidates if item[0] == latest_date]
    for earlier, later in pairwise(latest_period):
        if (
            earlier[1].available_at.value >= later[1].available_at.value
            or later[2].supersedes_content_hash != earlier[2].content_hash
        ):
            raise ValueError("shares observations have an ambiguous latest authority")
    facts.sort(
        key=lambda fact: (
            fact.concept.value,
            fact.fiscal_year,
            fact.period_end.value,
            fact.fiscal_period,
            fact.source_ref.record_id,
        )
    )
    return tuple(facts), shares_candidates[-1][1]


def factor_input_from_source_records(
    *,
    security_id: SecurityId,
    symbol: SecuritySymbol,
    identity: SecurityIdentityRecord,
    identities: tuple[SecurityIdentityRecord, ...],
    bar_record: NormalizedSourceRecord,
    sec_records: tuple[NormalizedSourceRecord, ...],
    sessions: tuple[MarketSession, ...],
    known_at: UtcTimestamp,
    split_adjustments: tuple[SplitAdjustment, ...] = (),
) -> FactorInput:
    """Build one factor input only through exact P4-A and P4-B authorities."""
    if identity.security_id != security_id or identity.symbol != symbol:
        raise ValueError("factor source identity does not match the requested security")
    closes = session_closes_from_record(
        bar_record,
        security_id=security_id,
        identities=identities,
        known_at=known_at,
    )
    facts, shares = sec_factor_inputs_from_records(
        sec_records,
        identity=identity,
        known_at=known_at,
    )
    if any(
        close._authority is None or close._authority.identity_hash != identity.identity_hash
        for close in closes
    ):
        raise ValueError("bar and SEC factor records resolve through different identities")
    return _finalize_factor_input(
        security_id=security_id,
        symbol=symbol,
        closes=closes,
        facts=facts,
        shares_outstanding=shares,
        sessions=sessions,
        split_adjustments=split_adjustments,
    )


@dataclass(frozen=True, slots=True)
class TtmFundamentals:
    """Four-quarter TTM values assembled from non-overlapping quarters."""

    ttm_net_income: Decimal
    ttm_cfo: Decimal
    ttm_capex: Decimal
    assets_at_ttm_start: Decimal
    assets_at_ttm_end: Decimal
    shares_outstanding: Decimal
    facts: tuple[QuarterlyFact, ...]

    def __post_init__(self) -> None:
        for name in (
            "ttm_net_income",
            "ttm_cfo",
            "ttm_capex",
            "assets_at_ttm_start",
            "assets_at_ttm_end",
            "shares_outstanding",
        ):
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")


def _fact_by_concept(
    facts: Sequence[QuarterlyFact],
    concept: FundamentalConcept,
    cutoff: UtcTimestamp,
    known_at: UtcTimestamp,
) -> list[QuarterlyFact]:
    return [
        fact
        for fact in facts
        if fact.concept is concept
        and fact.available_at.value <= known_at.value
        and fact.period_end.value <= cutoff.value.date()
    ]


def _quarter_value(fact: QuarterlyFact, prior_ytd: Decimal | None) -> Decimal:
    """Derive one quarter's value from a single-quarter or YTD fact.

    For a ``Q1``..``Q4`` fact the value is used as-is.  For a ``YTD`` fact
    the quarter value is ``YTD(current) - YTD(prior)`` with ``YTD(prior)``
    being zero for the first quarter.  This is the only allowed YTD
    derivation; anything else is ambiguous and must be handled by the caller
    returning ``None``.
    """
    if fact.fiscal_period == "YTD":
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            return fact.value - (prior_ytd or Decimal(0))
    return fact.value


def _unique_quarter_keys(
    facts: Sequence[QuarterlyFact],
    concept: FundamentalConcept,
    cutoff: UtcTimestamp,
    known_at: UtcTimestamp,
) -> tuple[QuarterlyFact, ...] | None:
    """Return the four most recent non-overlapping quarters for one concept.

    All four quarters must share the same entity, currency, consolidation
    scope, and fiscal year lineage; each quarter must be a distinct Q1..Q4 of
    the same fiscal year, and each fact must be visible by ``cutoff``.  YTD
    facts are supported when all four YTD facts exist for the same lineage;
    the quarter value is derived as ``YTD(current) - YTD(prior)``.
    """
    visible = _fact_by_concept(facts, concept, cutoff, known_at)
    if not visible:
        return None
    grouped: dict[tuple[str, str, str, int], list[QuarterlyFact]] = {}
    for fact in visible:
        key = (fact.entity, fact.currency, fact.consolidation, fact.fiscal_year)
        grouped.setdefault(key, []).append(fact)

    candidates: list[tuple[TradingDate, tuple[QuarterlyFact, ...]]] = []
    for group in grouped.values():
        periods = {fact.fiscal_period for fact in group}
        if "YTD" in periods and periods != {"YTD"}:
            direct_by_period: dict[str, QuarterlyFact] = {}
            ytd_by_period: dict[str, QuarterlyFact] = {}
            for fact in group:
                if fact.fiscal_period == "YTD":
                    period = fact.fiscal_quarter
                    if period is None or period in ytd_by_period:
                        return None
                    ytd_by_period[period] = fact
                else:
                    if fact.fiscal_period in direct_by_period:
                        return None
                    direct_by_period[fact.fiscal_period] = fact
            expected_periods = {"Q1", "Q2", "Q3", "Q4"}
            if set(direct_by_period) != expected_periods or set(ytd_by_period) != expected_periods:
                return None
            prior_ytd: Decimal | None = None
            for period in ("Q1", "Q2", "Q3", "Q4"):
                direct = direct_by_period[period]
                ytd = ytd_by_period[period]
                if direct.period_end != ytd.period_end or direct.value != _quarter_value(
                    ytd, prior_ytd
                ):
                    return None
                prior_ytd = ytd.value
        if periods == {"YTD"}:
            pure_ytd_by_period: dict[str, QuarterlyFact] = {}
            for fact in group:
                period = fact.fiscal_quarter
                if period is None or period in pure_ytd_by_period:
                    break
                pure_ytd_by_period[period] = fact
            else:
                if set(pure_ytd_by_period) == {"Q1", "Q2", "Q3", "Q4"}:
                    ordered = tuple(pure_ytd_by_period[p] for p in ("Q1", "Q2", "Q3", "Q4"))
                    period_ends = [fact.period_end.value for fact in ordered]
                    if period_ends == sorted(period_ends) and len(set(period_ends)) == 4:
                        candidates.append((ordered[-1].period_end, ordered))
        elif periods == {"Q1", "Q2", "Q3", "Q4"}:
            quarters: dict[str, QuarterlyFact] = {}
            for fact in group:
                if fact.fiscal_period in quarters:
                    break
                quarters[fact.fiscal_period] = fact
            else:
                ordered = tuple(quarters[p] for p in ("Q1", "Q2", "Q3", "Q4"))
                period_ends = [fact.period_end.value for fact in ordered]
                if period_ends == sorted(period_ends) and len(set(period_ends)) == 4:
                    candidates.append((ordered[-1].period_end, ordered))

    # A rolling TTM window normally crosses a fiscal-year boundary (for
    # example, Q2/Q3/Q4 of one year plus Q1 of the next).  Direct quarterly
    # facts therefore also need to be considered across fiscal years while
    # retaining the exact entity/unit/consolidation lineage.  YTD facts remain
    # scoped to one fiscal year because deriving a quarter requires the prior
    # YTD observation from that same year.
    direct_by_context: dict[tuple[str, str, str], list[QuarterlyFact]] = {}
    for fact in visible:
        if fact.fiscal_period != "YTD":
            direct_by_context.setdefault(
                (fact.entity, fact.currency, fact.consolidation), []
            ).append(fact)

    quarter_number = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
    for group in direct_by_context.values():
        by_key: dict[tuple[int, int], QuarterlyFact] = {}
        duplicate = False
        for fact in group:
            quarter_key = (fact.fiscal_year, quarter_number[fact.fiscal_period])
            if quarter_key in by_key:
                duplicate = True
                break
            by_key[quarter_key] = fact
        if duplicate:
            continue
        ordered_quarters = sorted(
            by_key.items(),
            key=lambda item: (item[0][0], item[0][1]),
        )
        for index in range(len(ordered_quarters) - 3):
            window = ordered_quarters[index : index + 4]
            ordinal = tuple(year * 4 + quarter for (year, quarter), _ in window)
            facts_window = tuple(fact for _, fact in window)
            period_ends = [fact.period_end.value for fact in facts_window]
            if (
                ordinal == tuple(range(ordinal[0], ordinal[0] + 4))
                and period_ends == sorted(period_ends)
                and len(set(period_ends)) == 4
            ):
                candidates.append((facts_window[-1].period_end, facts_window))

    if not candidates:
        return None
    latest_end = max(candidate[0].value for candidate in candidates)
    latest = {candidate[1] for candidate in candidates if candidate[0].value == latest_end}
    if len(latest) != 1:
        return None
    return latest.pop()


def assemble_ttm(
    facts: Sequence[QuarterlyFact],
    shares_outstanding: Decimal,
    *,
    cutoff: UtcTimestamp,
    known_at: UtcTimestamp | None = None,
) -> TtmFundamentals | None:
    """Assemble exact TTM fundamentals from four non-overlapping quarters.

    Returns ``None`` when the quarter lineage is incomplete or ambiguous.
    CapEx is expressed as a positive cash outflow; the factor layer never
    applies ``abs()`` to correct an unknown sign.
    """
    if (
        type(shares_outstanding) is not Decimal
        or not shares_outstanding.is_finite()
        or shares_outstanding <= 0
    ):
        raise ValueError("shares_outstanding must be a positive finite Decimal")
    if type(cutoff) is not UtcTimestamp:
        raise ValueError("cutoff requires canonical UTC")
    if known_at is None:
        known_at = cutoff
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC or None")
    if known_at.value > cutoff.value:
        raise ValueError("known_at cannot be after cutoff")
    if type(facts) is not tuple and not isinstance(facts, (list, tuple)):
        raise ValueError("facts must be a sequence of QuarterlyFact values")
    facts_tuple = tuple(facts)
    if any(type(f) is not QuarterlyFact for f in facts_tuple):
        raise ValueError("facts require exact QuarterlyFact values")
    if (
        any(type(fact.security_id) is not SecurityId for fact in facts_tuple)
        or len({fact.security_id for fact in facts_tuple}) > 1
    ):
        return None

    net_income_quarters = _unique_quarter_keys(
        facts_tuple, FundamentalConcept.NET_INCOME_LOSS, cutoff, known_at
    )
    cfo_quarters = _unique_quarter_keys(
        facts_tuple, FundamentalConcept.NET_CASH_OPERATING, cutoff, known_at
    )
    capex_quarters = _unique_quarter_keys(
        facts_tuple, FundamentalConcept.CAPEX_PPE, cutoff, known_at
    )
    assets = _fact_by_concept(facts_tuple, FundamentalConcept.ASSETS, cutoff, known_at)
    if net_income_quarters is None or cfo_quarters is None or capex_quarters is None:
        return None

    lineage = net_income_quarters[0]
    expected_quarter_keys = [
        (fact.fiscal_year, fact.fiscal_quarter or fact.fiscal_period, fact.period_end)
        for fact in net_income_quarters
    ]
    for quarters in (cfo_quarters, capex_quarters):
        if any(
            (
                fact.entity,
                fact.currency,
                fact.consolidation,
            )
            != (
                lineage.entity,
                lineage.currency,
                lineage.consolidation,
            )
            for fact in quarters
        ):
            return None
        if [
            (fact.fiscal_year, fact.fiscal_quarter or fact.fiscal_period, fact.period_end)
            for fact in quarters
        ] != expected_quarter_keys:
            return None

    def _quarter_values(quarters: tuple[QuarterlyFact, ...]) -> tuple[Decimal, ...]:
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            prior_ytd: Decimal | None = None
            values: list[Decimal] = []
            for q in quarters:
                if q.fiscal_period == "YTD":
                    values.append(_quarter_value(q, prior_ytd))
                    prior_ytd = q.value
                else:
                    values.append(q.value)
            return tuple(values)

    def _sum_ytd(quarters: tuple[QuarterlyFact, ...]) -> Decimal:
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            return sum(_quarter_values(quarters), Decimal(0))

    ttm_net_income = _sum_ytd(net_income_quarters)
    ttm_cfo = _sum_ytd(cfo_quarters)
    ttm_capex = _sum_ytd(capex_quarters)
    capex_quarter_values = _quarter_values(capex_quarters)
    if any(value < 0 for value in capex_quarter_values) or ttm_capex < 0:
        return None

    expected_lineage = (
        net_income_quarters[0].entity,
        net_income_quarters[0].currency,
        net_income_quarters[0].consolidation,
    )
    expected_asset_keys = {
        (fact.fiscal_year, fact.fiscal_quarter or fact.fiscal_period, fact.period_end)
        for fact in net_income_quarters
    }
    first_quarter = net_income_quarters[0]
    first_quarter_label = first_quarter.fiscal_quarter or first_quarter.fiscal_period
    quarter_number = int(first_quarter_label[1])
    start_fiscal_year = (
        first_quarter.fiscal_year if quarter_number > 1 else first_quarter.fiscal_year - 1
    )
    start_fiscal_period = f"Q{quarter_number - 1}" if quarter_number > 1 else "Q4"
    assets_by_period: dict[tuple[int, str, TradingDate], QuarterlyFact] = {}
    start_asset: QuarterlyFact | None = None
    for fact in assets:
        if (
            fact.entity,
            fact.currency,
            fact.consolidation,
        ) != expected_lineage:
            continue
        if fact.fiscal_period not in ("Q1", "Q2", "Q3", "Q4"):
            return None
        if (
            fact.fiscal_year == start_fiscal_year
            and fact.fiscal_period == start_fiscal_period
            and fact.period_end.value < first_quarter.period_end.value
        ):
            if start_asset is not None:
                return None
            start_asset = fact
            continue
        asset_key = (fact.fiscal_year, fact.fiscal_period, fact.period_end)
        if asset_key not in expected_asset_keys:
            continue
        if asset_key in assets_by_period:
            return None
        assets_by_period[asset_key] = fact
    if start_asset is None or set(assets_by_period) != expected_asset_keys:
        return None
    ordered_assets = tuple(assets_by_period[key] for key in expected_quarter_keys)
    if [fact.period_end for fact in ordered_assets] != [
        fact.period_end for fact in net_income_quarters
    ]:
        return None
    assets_at_ttm_start = start_asset.value
    assets_at_ttm_end = ordered_assets[-1].value
    if assets_at_ttm_start <= 0 or assets_at_ttm_end <= 0:
        return None

    return TtmFundamentals(
        ttm_net_income=ttm_net_income,
        ttm_cfo=ttm_cfo,
        ttm_capex=ttm_capex,
        assets_at_ttm_start=assets_at_ttm_start,
        assets_at_ttm_end=assets_at_ttm_end,
        shares_outstanding=shares_outstanding,
        facts=tuple(ordered_assets),
    )


def adjusted_closes(
    closes: Sequence[SessionClose],
    split_adjustments: Sequence[SplitAdjustment] = (),
    *,
    cutoff: UtcTimestamp,
    sessions: tuple[MarketSession, ...],
    security_id: SecurityId | None = None,
    known_at: UtcTimestamp | None = None,
) -> tuple[SessionClose, ...] | None:
    """Return split-aware point-in-time closes visible by ``cutoff``.

    Applies only confirmed adjustments whose ``available_at`` is ≤ cutoff;
    future corporate-action adjustments are never applied.  Bars are accepted
    only when they are strictly ordered by session and unique.
    """
    if type(cutoff) is not UtcTimestamp:
        raise ValueError("cutoff requires canonical UTC")
    if known_at is None:
        known_at = cutoff
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC or None")
    if known_at.value > cutoff.value:
        raise ValueError("known_at cannot be after cutoff")
    if security_id is not None and type(security_id) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId or None")
    session_by_date = _session_calendar(sessions)
    if type(closes) is not tuple and not isinstance(closes, (list, tuple)):
        raise ValueError("closes must be a sequence of SessionClose values")
    closes_tuple = tuple(closes)
    if any(type(c) is not SessionClose for c in closes_tuple):
        raise ValueError("closes require exact SessionClose values")
    if security_id is not None and any(
        type(close.security_id) is not SecurityId or close.security_id != security_id
        for close in closes_tuple
    ):
        return None
    close_dates = [c.trading_date.value for c in closes_tuple]
    if close_dates != sorted(close_dates) or len(set(close_dates)) != len(close_dates):
        return None
    if any(
        close.trading_date not in session_by_date
        or session_by_date[close.trading_date].day_kind is MarketDayKind.CLOSED
        or session_by_date[close.trading_date].regular_session is None
        for close in closes_tuple
    ):
        return None
    if any(
        trading_date >= cutoff.value.date() or close.available_at.value > known_at.value
        for trading_date, close in zip(close_dates, closes_tuple, strict=True)
    ):
        return None
    if type(split_adjustments) is not tuple and not isinstance(split_adjustments, (list, tuple)):
        raise ValueError("split_adjustments must be a sequence of SplitAdjustment values")
    split_tuple = tuple(split_adjustments)
    if any(type(split) is not SplitAdjustment for split in split_tuple):
        raise ValueError("split_adjustments require exact SplitAdjustment values")
    split_dates = [split.ex_date.value for split in split_tuple]
    if len(split_dates) != len(set(split_dates)):
        return None
    if split_tuple and (
        security_id is None
        or any(split.security_id != security_id or not split.confirmed for split in split_tuple)
    ):
        return None

    visible_splits = tuple(
        split
        for split in split_tuple
        if split.available_at.value <= known_at.value and split.ex_date.value <= cutoff.value.date()
    )
    adjusted: list[SessionClose] = []
    for close in closes_tuple:
        value = _adjusted_close_value(close.close, close.trading_date, visible_splits)
        adjusted.append(
            _finalize_session_close(
                trading_date=close.trading_date,
                close=value,
                source_ref=close.source_ref,
                available_at=close.available_at,
                security_id=close.security_id,
            )
        )
    return tuple(adjusted)


def _simple_returns(closes: Sequence[SessionClose]) -> list[Decimal] | None:
    """Return the simple returns of consecutive closes, in order."""
    if len(closes) < 2:
        return None
    returns: list[Decimal] = []
    for earlier, later in pairwise(closes):
        if later.close <= 0 or earlier.close <= 0:
            return None
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            returns.append(later.close / earlier.close - 1)
    return returns


def _trend(closes: Sequence[SessionClose], lookback: int) -> Decimal | None:
    """Return ``P(t-21) / P(t-lookback) - 1`` given ascending closes.

    ``closes[-1]`` is ``P(t-1)``, the previous completed session close, so
    ``P(t-lookback)`` is ``closes[-lookback]``.
    """
    if len(closes) < lookback or len(closes) < 21:
        return None
    reference = closes[-21].close
    base = closes[-lookback].close
    if reference <= 0 or base <= 0:
        return None
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        return reference / base - 1


def _vol63(closes: Sequence[SessionClose]) -> Decimal | None:
    """Return population annualized volatility over the last 63 returns."""
    returns = _simple_returns(closes)
    if returns is None or len(returns) < 63:
        return None
    recent = returns[-63:]
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        mean = sum(recent, Decimal(0)) / 63
        variance = sum(((r - mean) ** 2 for r in recent), Decimal(0)) / 63
        if variance < 0:
            return None
        return variance.sqrt() * Decimal(252).sqrt()


def _max_drawdown252(closes: Sequence[SessionClose]) -> Decimal | None:
    """Return the 252-session maximum drawdown in [0, 1)."""
    if len(closes) < 252:
        return None
    recent = closes[-252:]
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    for close in recent:
        if close.close <= 0:
            return None
        if close.close > peak:
            peak = close.close
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            drawdown = 1 - close.close / peak if peak > 0 else Decimal(0)
        if drawdown > max_drawdown:
            max_drawdown = drawdown
    if max_drawdown >= 1:
        return None
    return max_drawdown


def _raw_subfactors(
    *,
    closes: tuple[SessionClose, ...],
    ttm: TtmFundamentals | None,
    previous_close: Decimal | None,
) -> dict[str, Decimal] | None:
    """Compute all nine mandatory raw subfactors, or None on any missing input."""
    if ttm is None or previous_close is None:
        return None
    trend_126_21 = _trend(closes, 126)
    trend_252_21 = _trend(closes, 252)
    vol63 = _vol63(closes)
    max_drawdown252 = _max_drawdown252(closes)
    if trend_126_21 is None or trend_252_21 is None or vol63 is None or max_drawdown252 is None:
        return None

    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        average_assets = (ttm.assets_at_ttm_start + ttm.assets_at_ttm_end) / 2
        if average_assets <= 0 or ttm.shares_outstanding <= 0 or previous_close <= 0:
            return None
        market_cap = ttm.shares_outstanding * previous_close
        if market_cap <= 0:
            return None

        return {
            "trend_126_21": trend_126_21,
            "trend_252_21": trend_252_21,
            "roa": ttm.ttm_net_income / average_assets,
            "cfo_to_assets": ttm.ttm_cfo / average_assets,
            "accrual_quality": (ttm.ttm_cfo - ttm.ttm_net_income) / average_assets,
            "earnings_yield": ttm.ttm_net_income / market_cap,
            "fcf_yield": (ttm.ttm_cfo - ttm.ttm_capex) / market_cap,
            "vol63": vol63,
            "max_drawdown_252": max_drawdown252,
        }


def _percentile(values: Sequence[Decimal]) -> list[Decimal]:
    """Winsorize at 5%/95% and convert to midrank percentiles in [0, 1].

    ``q(p) = x[ceil(p*N) - 1]`` on the ascending sorted raw values; each raw
    value is clamped into [q05, q95]; percentiles are computed over the
    winsorized ascending values so ties receive identical midrank values.
    """
    n = len(values)
    if n == 0:
        raise ValueError("percentile requires a non-empty cross-section")
    if n == 1:
        return [Decimal("0.5")]
    ordered = sorted(values)
    # Integer arithmetic is deliberate: the manifest's ceil(p*N) boundary
    # must not depend on binary-float representation at N=20, 100, etc.
    q05 = ordered[(5 * n + 99) // 100 - 1]
    q95 = ordered[(95 * n + 99) // 100 - 1]
    winsorized = [min(max(value, q05), q95) for value in values]
    winsorized_sorted = sorted(winsorized)
    result: list[Decimal] = []
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        for value in winsorized:
            first = winsorized_sorted.index(value)
            last = len(winsorized_sorted) - 1 - winsorized_sorted[::-1].index(value)
            result.append((Decimal(first) + Decimal(last)) / Decimal(2) / Decimal(n - 1))
    return result


def _category_scores(
    raw_by_security: Mapping[str, dict[str, Decimal]],
) -> dict[str, dict[str, Decimal]]:
    """Compute category and composite scores over the complete cross-section.

    Percentiles are computed per raw subfactor across the full factor-eligible
    cross-section, so no security is scored against a partial population.
    Volatility and max drawdown are negated before percentile conversion so a
    high score means lower risk.
    """
    if not raw_by_security:
        return {}
    subfactor_names = (
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
    security_ids = sorted(raw_by_security)
    percentiles: dict[str, dict[str, Decimal]] = {}
    for name in subfactor_names:
        column = [raw_by_security[sid][name] for sid in security_ids]
        if name in ("vol63", "max_drawdown_252"):
            column = [-value for value in column]
        converted = _percentile(column)
        percentiles[name] = {sid: converted[index] for index, sid in enumerate(security_ids)}

    scores: dict[str, dict[str, Decimal]] = {}
    for sid in security_ids:
        p = percentiles
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            trend = (p["trend_126_21"][sid] + p["trend_252_21"][sid]) / 2
            quality = (p["roa"][sid] + p["cfo_to_assets"][sid] + p["accrual_quality"][sid]) / 3
            value = (p["earnings_yield"][sid] + p["fcf_yield"][sid]) / 2
            low_risk = (p["vol63"][sid] + p["max_drawdown_252"][sid]) / 2
            composite = (
                Decimal("0.35") * trend
                + Decimal("0.25") * quality
                + Decimal("0.15") * value
                + Decimal("0.25") * low_risk
            )
        scores[sid] = {
            "trend": trend,
            "quality": quality,
            "value": value,
            "low_risk": low_risk,
            "composite": composite,
        }
    return scores


def _missing_vector(
    *,
    security_id: SecurityId,
    symbol: SecuritySymbol,
    manifest: FactorManifest,
    as_of: UtcTimestamp,
    known_at: UtcTimestamp,
    universe_hash: str,
    source_refs: tuple[SourceRef, ...],
    reason: str,
) -> FeatureVector:
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
    return _finalize_feature_vector(
        security_id=security_id,
        symbol=symbol,
        universe_hash=universe_hash,
        manifest_hash=manifest.manifest_hash,
        as_of=as_of,
        known_at=known_at,
        status=FactorStatus.FACTOR_INPUT_MISSING,
        raw=tuple(
            RawFeature(
                name=name,
                value=None,
                formula_version=_FORMULA_VERSION,
                source_refs=source_refs,
                security_id=security_id,
                missing_reason=reason,
            )
            for name in names
        ),
        trend=None,
        quality=None,
        value=None,
        low_risk=None,
        composite=None,
        missing_reason=reason,
        schema_version=_SCHEMA_VERSION,
    )


@dataclass(frozen=True, slots=True)
class FactorInput:
    """One security's complete factor input set."""

    security_id: SecurityId
    symbol: SecuritySymbol
    closes: tuple[SessionClose, ...]
    facts: tuple[QuarterlyFact, ...]
    shares_outstanding: SharesObservation
    sessions: tuple[MarketSession, ...]
    split_adjustments: tuple[SplitAdjustment, ...] = ()
    _authority: _RecordAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _RecordAuthority:
            raise ValueError("factor inputs must be finalized by the screening authority")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.closes) is not tuple or any(type(c) is not SessionClose for c in self.closes):
            raise ValueError("closes must be a tuple of SessionClose values")
        if type(self.facts) is not tuple or any(type(f) is not QuarterlyFact for f in self.facts):
            raise ValueError("facts must be a tuple of QuarterlyFact values")
        if type(self.shares_outstanding) is not SharesObservation:
            raise ValueError("shares_outstanding requires an exact SEC SharesObservation")
        if any(
            type(close.security_id) is not SecurityId or close.security_id != self.security_id
            for close in self.closes
        ):
            raise ValueError("session closes must bind to the factor security")
        if any(
            type(fact.security_id) is not SecurityId or fact.security_id != self.security_id
            for fact in self.facts
        ):
            raise ValueError("quarterly facts must bind to the factor security")
        if (
            type(self.shares_outstanding.security_id) is not SecurityId
            or self.shares_outstanding.security_id != self.security_id
        ):
            raise ValueError("shares observation must bind to the factor security")
        _session_calendar(self.sessions)
        if type(self.split_adjustments) is not tuple or any(
            type(s) is not SplitAdjustment for s in self.split_adjustments
        ):
            raise ValueError("split_adjustments must be a tuple of SplitAdjustment values")
        if any(split.security_id != self.security_id for split in self.split_adjustments):
            raise ValueError("split adjustments must bind to the factor security")
        if (
            any(type(close._authority) is not _RecordAuthority for close in self.closes)
            or any(type(fact._authority) is not _RecordAuthority for fact in self.facts)
            or type(self.shares_outstanding._authority) is not _RecordAuthority
        ):
            raise ValueError("factor inputs require trusted source-record authorities")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        authority = self._authority
        assert type(authority) is _RecordAuthority
        for close in self.closes:
            close._verify_source_binding()
        for fact in self.facts:
            fact._verify_source_binding()
        self.shares_outstanding._verify_source_binding()
        for split in self.split_adjustments:
            split._verify_source_binding()
        record_authorities = [
            *(close._authority for close in self.closes),
            *(fact._authority for fact in self.facts),
            self.shares_outstanding._authority,
        ]
        identity_hashes = [
            record_authority.identity_hash
            for record_authority in record_authorities
            if type(record_authority) is _RecordAuthority
        ]
        if any(value is not None for value in identity_hashes) and (
            any(value is None for value in identity_hashes) or len(set(identity_hashes)) != 1
        ):
            raise ValueError("factor source records do not share one P4-B identity lineage")
        bound_identity_hash = next((value for value in identity_hashes if value is not None), None)
        if bound_identity_hash is not None and any(
            split.security_identity_hash != bound_identity_hash for split in self.split_adjustments
        ):
            raise ValueError("factor split lineage does not match the source identity")
        if authority.fingerprint != _factor_input_fingerprint(self):
            raise ValueError("factor-input authority is not bound to frozen content")

    @property
    def source_refs(self) -> tuple[SourceRef, ...]:
        """Return the deterministic source lineage used by every raw factor."""
        refs = {
            *(close.source_ref for close in self.closes),
            *(fact.source_ref for fact in self.facts),
            self.shares_outstanding.source_ref,
            *(split.source_ref for split in self.split_adjustments),
        }
        return tuple(
            sorted(refs, key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash))
        )

    def source_refs_at(
        self, *, as_of: UtcTimestamp, known_at: UtcTimestamp
    ) -> tuple[SourceRef, ...]:
        """Return only source refs visible at the factor replay cutoff.

        Future filings, late corrections, and scheduled corporate actions may
        remain in the input batch for audit, but they must not perturb the
        historical feature hash when they were not visible or effective at
        this cutoff.
        """
        if type(as_of) is not UtcTimestamp or type(known_at) is not UtcTimestamp:
            raise ValueError("as_of and known_at require canonical UTC")
        if known_at.value > as_of.value:
            raise ValueError("known_at cannot be after as_of")
        refs = {
            close.source_ref
            for close in self.closes
            if close.trading_date.value < as_of.value.date()
            and close.available_at.value <= known_at.value
        }
        refs.update(
            fact.source_ref
            for fact in self.facts
            if fact.period_end.value <= as_of.value.date()
            and fact.available_at.value <= known_at.value
        )
        if self.shares_outstanding.available_at.value <= known_at.value:
            refs.add(self.shares_outstanding.source_ref)
        refs.update(
            split.source_ref
            for split in self.split_adjustments
            if split.ex_date.value <= as_of.value.date()
            and split.available_at.value <= known_at.value
        )
        # A fully future-only input is already missing and still needs a
        # typed audit anchor; it cannot become eligible from this fallback.
        if not refs:
            refs = set(self.source_refs)
        return tuple(
            sorted(refs, key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash))
        )


def _factor_input_fingerprint(value: FactorInput) -> tuple[object, ...]:
    return (
        value.security_id,
        value.symbol,
        value.closes,
        value.facts,
        value.shares_outstanding,
        value.sessions,
        value.split_adjustments,
    )


def _finalize_factor_input(**values: object) -> FactorInput:
    """Finalize factor inputs after normalized source payload assembly."""
    body = dict(values)
    body.pop("_authority", None)
    body.setdefault("split_adjustments", ())
    closes = body.get("closes")
    facts = body.get("facts")
    shares = body.get("shares_outstanding")
    if type(closes) is not tuple or any(not isinstance(value, SessionClose) for value in closes):
        raise ValueError("factor input closes require trusted SessionClose values")
    if type(facts) is not tuple or any(not isinstance(value, QuarterlyFact) for value in facts):
        raise ValueError("factor input facts require trusted QuarterlyFact values")
    if not isinstance(shares, SharesObservation):
        raise ValueError("factor input shares require a trusted SharesObservation")
    provisional = object.__new__(FactorInput)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    body["_authority"] = _RecordAuthority(_factor_input_fingerprint(provisional))
    return FactorInput(**body)  # type: ignore[arg-type]


def _reconstruct_factor_input(**values: object) -> FactorInput:
    """Reconstruct factor inputs after DB/source-record payload validation."""
    return _finalize_factor_input(**values)


def _validate_universe_available_for_cutoff(
    universe: UniverseSnapshot,
    cutoff: UtcTimestamp,
) -> None:
    """Enforce the monthly-universe availability contract for one screening cutoff.

    The universe ``as_of`` is the month's first open NYSE session.  It serves
    later daily cutoffs in that month only after it became known.
    """
    universe.verify_integrity()
    if (
        universe.as_of.value.year != cutoff.value.year
        or universe.as_of.value.month != cutoff.value.month
        or universe.as_of.value > cutoff.value.date()
    ):
        raise ValueError("universe month does not match screening cutoff")
    if universe.known_at.value > cutoff.value:
        raise ValueError("universe was not available by screening cutoff")


def build_feature_vectors(
    inputs: Sequence[FactorInput],
    *,
    as_of: UtcTimestamp,
    known_at: UtcTimestamp,
    universe: UniverseSnapshot,
    manifest: FactorManifest | None = None,
) -> tuple[FeatureVector, ...]:
    """Evaluate every eligible security's factor features over one cross-section.

    All nine raw subfactors are mandatory.  Any missing, conflicting, or
    future input makes that security ``FACTOR_INPUT_MISSING``; it never
    enters the quant set and its low score can never be silently masked.
    """
    if manifest is None:
        manifest = factor_manifest()
    if type(manifest) is not FactorManifest:
        raise ValueError("manifest requires an exact FactorManifest")
    if manifest.manifest_hash != factor_manifest().manifest_hash:
        raise ValueError("feature-vector build requires the approved factor manifest")
    if type(as_of) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    if type(universe) is not UniverseSnapshot:
        raise ValueError("universe requires an exact UniverseSnapshot")
    _validate_universe_available_for_cutoff(universe, as_of)
    if universe.known_at.value > known_at.value:
        raise ValueError("universe is not visible at the feature-vector cutoff")
    if known_at.value > as_of.value:
        raise ValueError("known_at cannot be after as_of")
    if type(inputs) is not tuple and not isinstance(inputs, (list, tuple)):
        raise ValueError("inputs must be a sequence of FactorInput values")
    inputs_tuple = tuple(inputs)
    if any(type(item) is not FactorInput for item in inputs_tuple):
        raise ValueError("inputs require exact FactorInput values")
    for item in inputs_tuple:
        item._verify_source_binding()
    if len({item.security_id.value for item in inputs_tuple}) != len(inputs_tuple):
        raise ValueError("factor inputs must not repeat a security")
    # One exchange has one calendar.  Calendars may differ in *depth* (a
    # security with shorter history legitimately supplies fewer sessions), but
    # on any date two calendars both describe, the records must agree -- a
    # private "holiday" (REGULAR vs CLOSED on the same date) would silently
    # desynchronise each security's 252-session window inside one cross-section.
    if len(inputs_tuple) > 1:
        reference_calendar: dict[TradingDate, MarketSession] = {}
        for item in inputs_tuple:
            for trading_date, session in _session_calendar(item.sessions).items():
                seen = reference_calendar.setdefault(trading_date, session)
                if seen != session:
                    raise ValueError(
                        "factor inputs must share one explicit market-session calendar"
                    )

    raw_by_security: dict[str, dict[str, Decimal]] = {}
    closed: dict[str, FeatureVector] = {}
    previous_close: dict[str, Decimal] = {}
    source_refs_by_security: dict[str, tuple[SourceRef, ...]] = {}
    price_session_dates_by_security: dict[str, tuple[TradingDate, ...]] = {}
    universe_entries = {entry.security_id.value: entry for entry in universe.eligible_entries}
    if {item.security_id.value for item in inputs_tuple} != set(universe_entries):
        raise ValueError("factor inputs must cover exactly the eligible universe")
    for item in inputs_tuple:
        source_refs = item.source_refs_at(as_of=as_of, known_at=known_at)
        source_refs_by_security[item.security_id.value] = source_refs
        universe_entry = universe_entries.get(item.security_id.value)
        if universe_entry is None or universe_entry.symbol != item.symbol:
            closed[item.security_id.value] = _missing_vector(
                security_id=item.security_id,
                symbol=item.symbol,
                manifest=manifest,
                as_of=as_of,
                known_at=known_at,
                universe_hash=universe.universe_hash,
                source_refs=source_refs,
                reason="security is not an eligible member of the universe",
            )
            continue
        closes = adjusted_closes(
            item.closes,
            item.split_adjustments,
            cutoff=as_of,
            sessions=item.sessions,
            security_id=item.security_id,
            known_at=known_at,
        )
        if (
            closes is None
            or not _has_latest_session_coverage(
                tuple(close.trading_date for close in closes),
                _session_calendar(item.sessions),
                cutoff_date=as_of.value.date(),
                count=_FACTOR_SESSION_WINDOW,
            )
            or item.shares_outstanding.available_at.value > known_at.value
        ):
            closed[item.security_id.value] = _missing_vector(
                security_id=item.security_id,
                symbol=item.symbol,
                manifest=manifest,
                as_of=as_of,
                known_at=known_at,
                universe_hash=universe.universe_hash,
                source_refs=source_refs,
                reason="insufficient or unordered session closes",
            )
            continue
        applied_split_available = max(
            (
                split.available_at.value
                for split in item.split_adjustments
                if split.available_at.value <= known_at.value
                and split.ex_date.value <= as_of.value.date()
            ),
            default=None,
        )
        if (
            applied_split_available is not None
            and item.shares_outstanding.available_at.value < applied_split_available
        ):
            # Market cap divides a split-adjusted price by a share count: a
            # share observation received before the split became known cannot
            # be trusted to sit on the same share basis as the adjusted prices.
            closed[item.security_id.value] = _missing_vector(
                security_id=item.security_id,
                symbol=item.symbol,
                manifest=manifest,
                as_of=as_of,
                known_at=known_at,
                universe_hash=universe.universe_hash,
                source_refs=source_refs,
                reason="shares observation predates an applied split",
            )
            continue
        assert closes is not None
        price_session_dates_by_security[item.security_id.value] = tuple(
            close.trading_date for close in closes[-_FACTOR_SESSION_WINDOW:]
        )
        ttm = assemble_ttm(
            item.facts,
            item.shares_outstanding.value,
            cutoff=as_of,
            known_at=known_at,
        )
        if ttm is not None:
            anchor = ttm.facts[0]
            shares_lineage = (
                item.shares_outstanding.entity,
                item.shares_outstanding.currency,
                item.shares_outstanding.consolidation,
            )
            if shares_lineage != (anchor.entity, anchor.currency, anchor.consolidation):
                ttm = None
        raw = _raw_subfactors(closes=closes, ttm=ttm, previous_close=closes[-1].close)
        if raw is None:
            closed[item.security_id.value] = _missing_vector(
                security_id=item.security_id,
                symbol=item.symbol,
                manifest=manifest,
                as_of=as_of,
                known_at=known_at,
                universe_hash=universe.universe_hash,
                source_refs=source_refs,
                reason="mandatory factor input missing or conflicting",
            )
            continue
        raw_by_security[item.security_id.value] = raw
        previous_close[item.security_id.value] = closes[-1].close

    scores = _category_scores(raw_by_security)
    vectors: list[FeatureVector] = []
    for item in inputs_tuple:
        sid = item.security_id.value
        if sid in closed:
            vectors.append(closed[sid])
            continue
        score = scores[sid]
        raw_values = raw_by_security[sid]
        names = tuple(raw_values)
        source_refs = source_refs_by_security[sid]
        vectors.append(
            _finalize_feature_vector(
                security_id=item.security_id,
                symbol=item.symbol,
                universe_hash=universe.universe_hash,
                manifest_hash=manifest.manifest_hash,
                as_of=as_of,
                known_at=known_at,
                status=FactorStatus.COMPLETE,
                raw=tuple(
                    RawFeature(
                        name=name,
                        value=raw_values[name],
                        formula_version=_FORMULA_VERSION,
                        source_refs=source_refs,
                        security_id=item.security_id,
                    )
                    for name in names
                ),
                trend=score["trend"],
                quality=score["quality"],
                value=score["value"],
                low_risk=score["low_risk"],
                composite=score["composite"],
                missing_reason=None,
                schema_version=_SCHEMA_VERSION,
                price_session_dates=price_session_dates_by_security[sid],
            )
        )
    return tuple(sorted(vectors, key=lambda v: v.security_id.value))


def _tie_break_key(entry: CandidateEntry) -> tuple[Decimal, ...]:
    """Return the canonical ordering key (descending scores, then id)."""
    return (
        entry.composite,
        entry.trend,
        entry.quality,
        entry.value,
        entry.low_risk,
    )


def _validated_candidate_sequence(
    entries: Sequence[CandidateEntry], *, stage: CandidateStage, label: str
) -> tuple[CandidateEntry, ...]:
    """Validate a stage boundary before any dict-based lookup or truncation."""
    if type(entries) is not tuple and not isinstance(entries, (list, tuple)):
        raise ValueError(f"{label} must be a sequence of CandidateEntry values")
    result = tuple(entries)
    if any(type(entry) is not CandidateEntry for entry in result):
        raise ValueError(f"{label} requires exact CandidateEntry values")
    ids = [entry.security_id.value for entry in result]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{label} must not repeat a security")
    if any(entry.stage is not stage for entry in result):
        raise ValueError(f"{label} contains an entry from the wrong stage")
    expected = tuple(
        sorted(
            result,
            key=lambda entry: (
                *((-value) for value in _tie_break_key(entry)),
                entry.security_id.value,
            ),
        )
    )
    if result != expected:
        raise ValueError(f"{label} must use the canonical score order")
    return result


def quant_candidates(
    vectors: Sequence[FeatureVector],
    *,
    universe: UniverseSnapshot,
    manifest: FactorManifest | None = None,
) -> tuple[CandidateEntry, ...]:
    """Return the quant top-100 from complete feature vectors.

    Sorting happens only after every eligible name is scored.  Ties are
    broken by composite, trend, quality, value, low_risk descending, then
    stable security id ascending.
    """
    if manifest is None:
        manifest = factor_manifest()
    if type(manifest) is not FactorManifest:
        raise ValueError("manifest requires an exact FactorManifest")
    if manifest.manifest_hash != factor_manifest().manifest_hash:
        raise ValueError("quant candidates require the approved factor manifest")
    if type(universe) is not UniverseSnapshot:
        raise ValueError("universe requires an exact UniverseSnapshot")
    universe.verify_integrity()
    if type(vectors) is not tuple and not isinstance(vectors, (list, tuple)):
        raise ValueError("vectors must be a sequence of FeatureVector values")
    vectors_tuple = tuple(vectors)
    if any(type(vector) is not FeatureVector for vector in vectors_tuple):
        raise ValueError("vectors require exact FeatureVector values")
    vector_ids = [vector.security_id.value for vector in vectors_tuple]
    if len(vector_ids) != len(set(vector_ids)):
        raise ValueError("vectors must not repeat a security")
    eligible_ids = {entry.security_id.value for entry in universe.eligible_entries}
    if set(vector_ids) != eligible_ids:
        raise ValueError("feature vectors must cover exactly the eligible universe")
    if any(
        vector.status is FactorStatus.COMPLETE and vector.manifest_hash != manifest.manifest_hash
        for vector in vectors_tuple
    ):
        raise ValueError("complete vector uses a factor manifest other than the approved manifest")
    if any(
        vector.status is FactorStatus.COMPLETE and vector.universe_hash != universe.universe_hash
        for vector in vectors_tuple
    ):
        raise ValueError("complete vector uses a different universe")
    if any(
        vector.status is FactorStatus.COMPLETE and vector.known_at.value < universe.known_at.value
        for vector in vectors_tuple
    ):
        raise ValueError("complete vector predates the universe knowledge cutoff")
    eligible = {entry.security_id.value: entry for entry in universe.eligible_entries}
    complete = [v for v in vectors_tuple if v.status is FactorStatus.COMPLETE]
    raw_by_security = {
        vector.security_id.value: {
            raw.name: raw.value for raw in vector.raw if raw.value is not None
        }
        for vector in complete
    }
    expected_scores = _category_scores(raw_by_security)
    for vector in complete:
        expected = expected_scores[vector.security_id.value]
        if any(
            getattr(vector, name) != expected[name]
            for name in ("trend", "quality", "value", "low_risk", "composite")
        ):
            raise ValueError(
                "complete vector scores must be recomputed from the approved raw factors"
            )
    entries: list[CandidateEntry] = []
    for vector in complete:
        universe_entry = eligible.get(vector.security_id.value)
        if universe_entry is None or universe_entry.symbol != vector.symbol:
            raise ValueError("complete vector is not an eligible universe member")
        if universe_entry.quarantine_decision_hash is None:
            raise ValueError("eligible universe members require a quarantine decision reference")
        if (
            vector.composite is None
            or vector.trend is None
            or vector.quality is None
            or vector.value is None
            or vector.low_risk is None
        ):
            continue
        entries.append(
            _finalize_candidate_entry(
                security_id=vector.security_id,
                symbol=vector.symbol,
                composite=vector.composite,
                trend=vector.trend,
                quality=vector.quality,
                value=vector.value,
                low_risk=vector.low_risk,
                stage=CandidateStage.QUANT,
                feature_hash=vector.feature_hash,
                universe_hash=vector.universe_hash,
                quarantine_decision_hash=universe_entry.quarantine_decision_hash,
            )
        )
    # Descending composite/trend/quality/value/low_risk, then ascending
    # stable security id.  Negative Decimal is exact, so the negated key is
    # canonical and deterministic.
    entries.sort(key=lambda e: (*((-value,) for value in _tie_break_key(e)), e.security_id.value))
    return tuple(entries[:QUANT_CAP])


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """Typed evidence gate inputs; free text and snippet sentiment never enter."""

    security_id: SecurityId
    authority_complete: bool
    evidence_fresh: bool
    evidence_conflict: bool
    prompt_injection_unresolved: bool
    quarantine_decision: QuarantineDecision
    evidence_source_refs: tuple[SourceRef, ...] = ()
    _authority: _EvidenceAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _EvidenceAuthority:
            raise ValueError("evidence views must be finalized by the evidence authority")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        for name in (
            "authority_complete",
            "evidence_fresh",
            "evidence_conflict",
            "prompt_injection_unresolved",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} requires an exact bool")
        if type(self.quarantine_decision) is not QuarantineDecision:
            raise ValueError("quarantine_decision requires an exact QuarantineDecision")
        self.quarantine_decision.verify_integrity()
        if type(self.evidence_source_refs) is not tuple or not self.evidence_source_refs:
            raise ValueError("evidence_source_refs require at least one typed SourceRef")
        if any(type(ref) is not SourceRef for ref in self.evidence_source_refs):
            raise ValueError("evidence_source_refs require exact SourceRef values")
        if len({ref.record_id for ref in self.evidence_source_refs}) != len(
            self.evidence_source_refs
        ):
            raise ValueError("evidence_source_refs must use unique record identifiers")
        if self.evidence_source_refs != tuple(
            sorted(
                self.evidence_source_refs,
                key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash),
            )
        ):
            raise ValueError("evidence_source_refs must use canonical order")
        if any(ref.family not in _EVIDENCE_SOURCE_FAMILIES for ref in self.evidence_source_refs):
            raise ValueError("evidence_source_refs must use an approved evidence authority")
        if self._authority.fingerprint != _evidence_view_fingerprint(self):
            raise ValueError("evidence-view authority is not bound to frozen content")


def _evidence_view_fingerprint(value: EvidenceView) -> tuple[object, ...]:
    return (
        value.security_id,
        value.authority_complete,
        value.evidence_fresh,
        value.evidence_conflict,
        value.prompt_injection_unresolved,
        value.quarantine_decision,
        value.evidence_source_refs,
    )


def _finalize_evidence_view(**values: object) -> EvidenceView:
    """Finalize typed evidence metadata from the trusted evidence assembler."""
    body = dict(values)
    body.pop("_authority", None)
    provisional = object.__new__(EvidenceView)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    body["_authority"] = _EvidenceAuthority(_evidence_view_fingerprint(provisional))
    return EvidenceView(**body)  # type: ignore[arg-type]


def _reconstruct_evidence_view(**values: object) -> EvidenceView:
    """Reconstruct typed evidence metadata after DB/source validation."""
    return _finalize_evidence_view(**values)


def evidence_candidates(
    quant: Sequence[CandidateEntry],
    evidence_views: Sequence[EvidenceView],
    *,
    sector_division: Mapping[str, SectorAssignment],
    identity_records: Mapping[str, SecurityIdentityRecord] | Sequence[SecurityIdentityRecord],
    as_of: UtcTimestamp,
    universe: UniverseSnapshot,
) -> tuple[CandidateEntry, ...]:
    """Return the evidence top-30 from the quant top-100.

    Only typed evidence metadata participates.  Material authority missing,
    future/stale/conflict evidence, unresolved prompt-injection flags, and a
    non-ELIGIBLE corporate-action quarantine all remove the candidate; no
    substitution from outside the quant set ever happens.
    """
    quant_tuple = _validated_candidate_sequence(quant, stage=CandidateStage.QUANT, label="quant")
    if type(universe) is not UniverseSnapshot:
        raise ValueError("universe requires an exact UniverseSnapshot")
    universe.verify_integrity()
    if type(evidence_views) is not tuple and not isinstance(evidence_views, (list, tuple)):
        raise ValueError("evidence_views must be a sequence of EvidenceView values")
    views_tuple = tuple(evidence_views)
    if any(type(view) is not EvidenceView for view in views_tuple):
        raise ValueError("evidence_views require exact EvidenceView values")
    view_ids = [view.security_id.value for view in views_tuple]
    if len(view_ids) != len(set(view_ids)):
        raise ValueError("evidence_views must not repeat a security")
    quant_ids = {entry.security_id.value for entry in quant_tuple}
    if any(view.security_id.value not in quant_ids for view in views_tuple):
        raise ValueError("evidence_views cannot introduce a security outside quant")
    if type(as_of) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    _validate_universe_available_for_cutoff(universe, as_of)

    if isinstance(identity_records, Mapping):
        identity_items = tuple(identity_records.items())
        if any(type(key) is not str for key, _ in identity_items):
            raise ValueError("identity_records mapping keys must be security-id text")
        identities = {key: value for key, value in identity_items}
        if len(identities) != len(identity_items):
            raise ValueError("identity_records must not repeat a security")
        if any(
            type(value) is not SecurityIdentityRecord or value.security_id.value != key
            for key, value in identity_items
        ):
            raise ValueError("identity_records mapping must bind exact identity records")
    elif type(identity_records) is tuple or isinstance(identity_records, list):
        identity_values = tuple(identity_records)
        if any(type(value) is not SecurityIdentityRecord for value in identity_values):
            raise ValueError("identity_records require exact SecurityIdentityRecord values")
        identities = {value.security_id.value: value for value in identity_values}
        if len(identities) != len(identity_values):
            raise ValueError("identity_records must not repeat a security")
    else:
        raise ValueError("identity_records require a mapping or sequence of identity records")

    sectors: dict[str, SicDivision] = {}
    for security_id, assignment in sector_division.items():
        if type(security_id) is not str:
            raise ValueError("sector_division keys must be security-id text")
        if type(assignment) is not SectorAssignment:
            raise ValueError("sector_division values require exact SEC SIC assignments")
        if assignment.security_id.value != security_id:
            raise ValueError("sector assignment security id does not match its mapping key")
        if assignment.taxonomy_hash != sector_manifest().manifest_hash:
            raise ValueError("sector assignment uses an unapproved taxonomy manifest")
        if (
            assignment.available_at.value > as_of.value
            or assignment.available_at.value > universe.known_at.value
        ):
            division = SicDivision.SECTOR_UNKNOWN
        else:
            try:
                division = SicDivision(assignment.division)
            except ValueError:
                division = SicDivision.SECTOR_UNKNOWN
        sectors[security_id] = division

    views = {view.security_id.value: view for view in views_tuple}
    universe_entries = {entry.security_id.value: entry for entry in universe.eligible_entries}
    kept: list[CandidateEntry] = []
    for entry in quant_tuple:
        view = views.get(entry.security_id.value)
        if view is None:
            continue
        if sectors.get(entry.security_id.value) in (None, SicDivision.SECTOR_UNKNOWN):
            continue
        if not view.authority_complete or not view.evidence_fresh:
            continue
        if view.evidence_conflict or view.prompt_injection_unresolved:
            continue
        universe_entry = universe_entries.get(entry.security_id.value)
        identity = identities.get(entry.security_id.value)
        candidate_assignment = sector_division.get(entry.security_id.value)
        if candidate_assignment is None:
            continue
        if (
            universe_entry is None
            or universe_entry.symbol != entry.symbol
            or universe_entry.quarantine_decision_hash != view.quarantine_decision.decision_hash
            or view.quarantine_decision.security_id != entry.security_id
            or view.quarantine_decision.symbol_as_of != entry.symbol
            or view.quarantine_decision.outcome is not QuarantineOutcome.ELIGIBLE
            or identity is None
            or identity.security_id != entry.security_id
            or identity.symbol != entry.symbol
            or identity.identity_hash != universe_entry.identity_hash
            or identity.status is not SecurityStatus.ACTIVE
            or identity.cik is None
            or not identity.answers_as_of(as_of=as_of, known_at=universe.known_at)
            or candidate_assignment.cik != identity.cik.value
        ):
            continue
        kept.append(
            _finalize_candidate_entry(
                security_id=entry.security_id,
                symbol=entry.symbol,
                composite=entry.composite,
                trend=entry.trend,
                quality=entry.quality,
                value=entry.value,
                low_risk=entry.low_risk,
                stage=CandidateStage.EVIDENCE,
                feature_hash=entry.feature_hash,
                universe_hash=entry.universe_hash,
                quarantine_decision_hash=entry.quarantine_decision_hash,
                sector_assignment_hash=candidate_assignment.assignment_hash,
                evidence_source_refs=view.evidence_source_refs,
            )
        )
        if len(kept) >= EVIDENCE_CAP:
            break
    return tuple(kept)


class FocusWindow(StrEnum):
    """The two daily focus windows with fixed caps."""

    OPEN_PLUS_60M = "OPEN_PLUS_60M"
    CLOSE_MINUS_90M = "CLOSE_MINUS_90M"


def select_focus_window(
    *,
    as_of: UtcTimestamp,
    session: MarketSession,
) -> FocusWindow:
    """Select the active focus window from the NYSE session and the as-of.

    The open window is active from the session open until
    ``open + 60m``; the close window from ``close - 90m`` until the close.
    Any other instant (outside the regular session or not inside either
    window) is rejected as ``WINDOW_OR_DEADLINE_INVALID`` by the caller;
    this function itself fails closed with a ValueError so a caller can
    never silently treat an out-of-window instant as a valid focus window.
    """
    from seven_lens.clock.market_clock import MarketDayKind, RegularSessionWindow

    if type(as_of) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    if type(session) is not MarketSession:
        raise ValueError("session requires an exact MarketSession")
    if session.day_kind is MarketDayKind.CLOSED or session.regular_session is None:
        raise ValueError("focus window requires an open market session")
    validate_nyse_session_window(session)
    window = session.regular_session
    if type(window) is not RegularSessionWindow:
        raise ValueError("focus window requires a regular-session window")
    open_deadline = UtcTimestamp(window.opens_at.value + timedelta(minutes=60))
    close_deadline = UtcTimestamp(window.closes_at.value - timedelta(minutes=90))
    if close_deadline.value <= open_deadline.value:
        raise ValueError("focus windows overlap for this market session")
    if window.opens_at.value <= as_of.value < open_deadline.value:
        return FocusWindow.OPEN_PLUS_60M
    if close_deadline.value <= as_of.value < window.closes_at.value:
        return FocusWindow.CLOSE_MINUS_90M
    raise ValueError("as_of is outside both focus windows")


def focus_candidates(
    evidence: Sequence[CandidateEntry],
    window: FocusWindow,
) -> tuple[CandidateEntry, ...]:
    """Truncate the evidence list to the window's fixed cap.

    OPEN_PLUS_60M takes at most 12; CLOSE_MINUS_90M takes at most 5.  The
    count is never padded to fill the cap.
    """
    if type(window) is not FocusWindow:
        raise ValueError("window requires an exact FocusWindow")
    evidence_tuple = _validated_candidate_sequence(
        evidence, stage=CandidateStage.EVIDENCE, label="evidence"
    )
    stage = (
        CandidateStage.FOCUS_OPEN
        if window is FocusWindow.OPEN_PLUS_60M
        else CandidateStage.FOCUS_CLOSE
    )
    return _expected_focus_from_evidence(evidence_tuple, stage=stage)


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    """One point-in-time return observation used by the cluster authority."""

    trading_date: TradingDate
    value: Decimal
    available_at: UtcTimestamp
    security_id: SecurityId | None = None
    source_ref: SourceRef | None = None
    _authority: _RecordAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _RecordAuthority:
            raise ValueError("return observations must be finalized by the market-data authority")
        if type(self.trading_date) is not TradingDate:
            raise ValueError("trading_date requires an exact TradingDate")
        if type(self.value) is not Decimal or not self.value.is_finite():
            raise ValueError("return value must be a finite Decimal")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId or None")
        if self.source_ref is not None and type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef or None")
        if self.source_ref is not None and (
            self.source_ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS
        ):
            raise ValueError("return observations require the historical-bars authority")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        """Re-check the private authority after an attempted mutation."""
        authority = self._authority
        assert type(authority) is _RecordAuthority
        if authority.fingerprint != _return_observation_fingerprint(self):
            raise ValueError("return-observation authority is not bound to frozen content")


def _return_observation_fingerprint(value: ReturnObservation) -> tuple[object, ...]:
    return (
        value.trading_date,
        value.value,
        value.available_at,
        value.security_id,
        value.source_ref,
    )


def _finalize_return_observation(**values: object) -> ReturnObservation:
    """Finalize a return from the trusted normalized market-data assembler."""
    body = dict(values)
    body.pop("_authority", None)
    body.setdefault("security_id", None)
    body.setdefault("source_ref", None)
    provisional = object.__new__(ReturnObservation)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    body["_authority"] = _RecordAuthority(_return_observation_fingerprint(provisional))
    return ReturnObservation(**body)  # type: ignore[arg-type]


def _reconstruct_return_observation(**values: object) -> ReturnObservation:
    """Reconstruct a return after DB/source-record payload validation."""
    return _finalize_return_observation(**values)


def return_observations_from_record(
    record: NormalizedSourceRecord,
    *,
    security_id: SecurityId,
    identities: tuple[SecurityIdentityRecord, ...],
    known_at: UtcTimestamp,
    sessions: tuple[MarketSession, ...],
    split_adjustments: tuple[SplitAdjustment, ...] = (),
) -> tuple[ReturnObservation, ...]:
    """Derive simple returns from one validated split-aware bars record."""
    if type(record) is not NormalizedSourceRecord:
        raise ValueError("record requires an exact NormalizedSourceRecord")
    if type(security_id) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")

    record.verify_integrity()
    if record.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS:
        raise ValueError("return observations require the historical-bars authority")
    if record.available_at is None or record.available_at.value > known_at.value:
        raise ValueError("historical-bars source is not available by known_at")

    closes = session_closes_from_record(
        record,
        security_id=security_id,
        identities=identities,
        known_at=known_at,
        split_adjustments=split_adjustments,
    )
    record_ref = SourceRef(record.record_id, record.family, record.record_hash)
    for close in closes:
        close._verify_source_binding()
        if (
            close.security_id != security_id
            or close.source_ref != record_ref
            or close.source_ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS
            or close.available_at.value > known_at.value
        ):
            raise ValueError("session close is not bound to the requested historical record")
        authority = close._authority
        if (
            type(authority) is not _RecordAuthority
            or authority.source_record_hash != record.record_hash
            or authority.identity_hash is None
        ):
            raise ValueError("session close is missing its source authority")

    session_by_date = _session_calendar(sessions)
    if closes:
        present_dates = {session.trading_date.value for session in sessions}
        current_date = closes[0].trading_date.value
        while current_date <= closes[-1].trading_date.value:
            if current_date.weekday() < 5 and current_date not in present_dates:
                raise ValueError("NYSE calendar window must include every weekday explicitly")
            current_date += timedelta(days=1)
    open_dates = tuple(
        session.trading_date
        for session in sessions
        if session.day_kind in (MarketDayKind.REGULAR, MarketDayKind.HALF_DAY)
        and closes
        and closes[0].trading_date.value
        <= session.trading_date.value
        <= closes[-1].trading_date.value
    )
    previous_open = {later: earlier for earlier, later in pairwise(open_dates)}

    observations: list[ReturnObservation] = []
    for previous, current in pairwise(closes):
        if current.trading_date.value <= previous.trading_date.value:
            raise ValueError("session closes must be strictly ordered by trading date")
        if previous.source_ref != current.source_ref:
            raise ValueError("adjacent session closes require one exact source ref")
        if previous_open.get(current.trading_date) != previous.trading_date:
            continue
        if (
            previous.trading_date not in session_by_date
            or current.trading_date not in session_by_date
        ):
            raise ValueError("return closes require explicit NYSE sessions")
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            value = current.close / previous.close - Decimal("1")
        current_authority = current._authority
        assert type(current_authority) is _RecordAuthority
        identity_hash = current_authority.identity_hash
        if identity_hash is None:
            raise ValueError("session close is missing its identity authority")
        values: dict[str, object] = {
            "trading_date": current.trading_date,
            "value": value,
            "available_at": max(
                (previous.available_at, current.available_at),
                key=lambda timestamp: timestamp.value,
            ),
            "security_id": security_id,
            "source_ref": current.source_ref,
        }
        observations.append(
            cast(
                ReturnObservation,
                _source_bound_record(
                    ReturnObservation,
                    _return_observation_fingerprint,
                    values,
                    source_record_hash=record.record_hash,
                    identity_hash=identity_hash,
                ),
            )
        )
    return tuple(observations)


def _pearson(ri: Sequence[Decimal], rj: Sequence[Decimal]) -> Decimal | None:
    """Return the Pearson correlation of two aligned return series."""
    if len(ri) != len(rj) or len(ri) < 100:
        return None
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        n = len(ri)
        mean_i = sum(ri, Decimal(0)) / n
        mean_j = sum(rj, Decimal(0)) / n
        cov = sum(
            ((a - mean_i) * (b - mean_j) for a, b in zip(ri, rj, strict=True)),
            Decimal(0),
        )
        var_i = sum(((a - mean_i) ** 2 for a in ri), Decimal(0))
        var_j = sum(((b - mean_j) ** 2 for b in rj), Decimal(0))
        if var_i <= 0 or var_j <= 0:
            return None
        denominator = (var_i * var_j).sqrt()
        if denominator == 0:
            return None
        rho = cov / denominator
        if not rho.is_finite():
            return None
        return rho


def _pearson_meets_threshold(
    ri: Sequence[Decimal], rj: Sequence[Decimal], threshold: Decimal
) -> bool:
    """Compare Pearson to a fixed threshold without a rounded sqrt boundary."""
    if len(ri) != len(rj) or len(ri) < 100:
        return False
    with localcontext(_SCREENING_DECIMAL_CONTEXT):
        n = len(ri)
        mean_i = sum(ri, Decimal(0)) / n
        mean_j = sum(rj, Decimal(0)) / n
        cov = sum(
            ((a - mean_i) * (b - mean_j) for a, b in zip(ri, rj, strict=True)),
            Decimal(0),
        )
        var_i = sum(((a - mean_i) ** 2 for a in ri), Decimal(0))
        var_j = sum(((b - mean_j) ** 2 for b in rj), Decimal(0))
        if cov < 0 or var_i <= 0 or var_j <= 0:
            return False
        return cov * cov >= threshold * threshold * var_i * var_j


def _cluster_id_for(
    *,
    policy_hash: str,
    as_of: UtcTimestamp,
    members: tuple[SecurityId, ...],
) -> str:
    canonical = json.dumps(
        {
            "policy_hash": policy_hash,
            "as_of": str(as_of),
            "members": [member.value for member in members],
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(_CLUSTER_ID_DOMAIN + canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class ClusterResult:
    """One correlation cluster: ordered members and the closed status."""

    cluster_id: str
    as_of: UtcTimestamp
    policy_hash: str
    manifest_hash: str
    members: tuple[SecurityId, ...]
    status: ClusterStatus
    source_refs: tuple[SourceRef, ...] = ()
    _authority: _ClusterAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _ClusterAuthority:
            raise ValueError("cluster results must be finalized by the cluster authority")
        if type(self.cluster_id) is not str or _HASH_TEXT.fullmatch(self.cluster_id) is None:
            raise ValueError("cluster_id must be a SHA-256 digest")
        if type(self.as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        for name in ("policy_hash", "manifest_hash"):
            value = getattr(self, name)
            if type(value) is not str or _HASH_TEXT.fullmatch(value) is None:
                raise ValueError(f"{name} must be a SHA-256 digest")
        if type(self.members) is not tuple or any(type(m) is not SecurityId for m in self.members):
            raise ValueError("members must be a tuple of SecurityId values")
        if len(self.members) > MAX_CLUSTER_MEMBERS:
            raise ValueError(f"cluster members require at most {MAX_CLUSTER_MEMBERS} values")
        if type(self.status) is not ClusterStatus:
            raise ValueError("status requires an exact ClusterStatus")
        if type(self.source_refs) is not tuple or any(
            type(ref) is not SourceRef for ref in self.source_refs
        ):
            raise ValueError("source_refs require exact SourceRef values")
        if len(self.source_refs) > MAX_CLUSTER_SOURCE_REFS:
            raise ValueError(
                f"cluster source_refs require at most {MAX_CLUSTER_SOURCE_REFS} values"
            )
        if len({ref.record_id for ref in self.source_refs}) != len(self.source_refs):
            raise ValueError("source_refs must use unique record identifiers")
        if self.source_refs != tuple(
            sorted(
                self.source_refs,
                key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash),
            )
        ):
            raise ValueError("source_refs must use canonical order")
        if any(ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS for ref in self.source_refs):
            raise ValueError("cluster source_refs require the historical-bars authority")
        if self.status is ClusterStatus.ASSIGNED and not self.source_refs:
            raise ValueError("assigned clusters require typed historical-bars source references")
        if not self.members:
            raise ValueError("cluster results must contain at least one member")
        if self.status is ClusterStatus.UNKNOWN and len(self.members) != 1:
            raise ValueError("UNKNOWN cluster results must identify exactly one member")
        if self.members != tuple(sorted(set(self.members), key=lambda member: member.value)):
            raise ValueError("cluster members must be sorted and unique")
        if self.cluster_id != _cluster_id_for(
            policy_hash=self.policy_hash,
            as_of=self.as_of,
            members=self.members,
        ):
            raise ValueError("cluster_id does not match the closed cluster content")
        if self.manifest_hash != cluster_manifest().manifest_hash:
            raise ValueError("cluster result requires the approved cluster manifest")
        if self._authority.cluster_id != self.cluster_id:
            raise ValueError("cluster authority is not bound to frozen content")
        self.wire()

    def wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "cluster_id": self.cluster_id,
            "as_of": str(self.as_of),
            "policy_hash": self.policy_hash,
            "manifest_hash": self.manifest_hash,
            "members": [member.value for member in self.members],
            "status": self.status.value,
            "source_refs": [
                {
                    "record_id": ref.record_id,
                    "family": ref.family.value,
                    "record_hash": ref.record_hash,
                }
                for ref in self.source_refs
            ],
        }
        _canonical_cluster_wire_bytes(wire)
        return wire

    def verify_integrity(self) -> bool:
        """Re-check the content-derived cluster id on PostgreSQL readback."""
        if self.cluster_id != _cluster_id_for(
            policy_hash=self.policy_hash,
            as_of=self.as_of,
            members=self.members,
        ):
            raise ValueError("cluster_id does not match the closed cluster content")
        return True


def _finalize_cluster_result(**values: object) -> ClusterResult:
    """Finalize one cluster from the trusted correlation assembler."""
    body = dict(values)
    body.pop("_authority", None)
    cluster_id = body.get("cluster_id")
    if type(cluster_id) is not str or _HASH_TEXT.fullmatch(cluster_id) is None:
        raise ValueError("cluster_id must be a SHA-256 digest")
    return ClusterResult(
        **body,  # type: ignore[arg-type]
        _authority=_ClusterAuthority(cluster_id),
    )


def _reconstruct_cluster_result(**values: object) -> ClusterResult:
    """Reconstruct one cluster after DB wire/hash validation."""
    body = dict(values)
    body.pop("_authority", None)
    cluster_id = body.get("cluster_id")
    if type(cluster_id) is not str or _HASH_TEXT.fullmatch(cluster_id) is None:
        raise ValueError("cluster_id must be a SHA-256 digest")
    return ClusterResult(
        **body,  # type: ignore[arg-type]
        _authority=_ClusterAuthority(cluster_id),
    )


def build_clusters(
    *,
    nodes: Sequence[SecurityId],
    returns: Mapping[str, tuple[ReturnObservation, ...]],
    policy_hash: str,
    as_of: UtcTimestamp,
    sessions: tuple[MarketSession, ...],
    manifest: ClusterManifest | None = None,
) -> tuple[ClusterResult, ...]:
    """Build connected-component correlation clusters over ordered nodes.

    Nodes are the union of the quant top-100 and current long holdings in
    stable security-id order.  Each security must contribute at least 100
    finite returns and each pair at least 100 common observations; Pearson
    correlation ≥ 0.75 creates an undirected edge.  A security with
    insufficient data is ``UNKNOWN`` and is never treated as a singleton.
    """
    if manifest is None:
        manifest = cluster_manifest()
    if type(manifest) is not ClusterManifest:
        raise ValueError("manifest requires an exact ClusterManifest")
    if manifest.manifest_hash != cluster_manifest().manifest_hash:
        raise ValueError("cluster build requires the approved cluster manifest")
    if type(policy_hash) is not str or _HASH_TEXT.fullmatch(policy_hash) is None:
        raise ValueError("policy_hash must be a SHA-256 digest")
    if type(as_of) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    session_by_date = _session_calendar(sessions)
    as_of_session = session_by_date.get(TradingDate(as_of.value.date()))
    if (
        as_of_session is None
        or as_of_session.day_kind is MarketDayKind.CLOSED
        or as_of_session.regular_session is None
    ):
        raise ValueError("cluster as_of requires an explicit open NYSE session")
    if type(nodes) is not tuple and not isinstance(nodes, (list, tuple)):
        raise ValueError("nodes must be a sequence of SecurityId values")
    nodes_tuple = tuple(nodes)
    if any(type(node) is not SecurityId for node in nodes_tuple):
        raise ValueError("nodes require exact SecurityId values")
    node_ids = [node.value for node in nodes_tuple]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("nodes must not repeat a security")
    ordered_nodes = tuple(sorted(nodes_tuple, key=lambda n: n.value))
    if not isinstance(returns, Mapping):
        raise ValueError("returns must be a mapping of security id to ReturnObservation values")

    by_id = {node.value: node for node in ordered_nodes}
    return_series: dict[str, list[Decimal]] = {}
    session_dates: dict[str, list[TradingDate]] = {}
    source_refs_by_security: dict[str, tuple[SourceRef, ...]] = {}
    for sid, series in returns.items():
        if sid not in by_id:
            continue
        if type(sid) is not str or (type(series) is not tuple and not isinstance(series, list)):
            continue
        if any(type(item) is not ReturnObservation for item in series):
            continue
        if any(
            item.security_id != by_id[sid]
            or item.source_ref is None
            or item.source_ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS
            for item in series
        ):
            continue
        if any(
            item._authority is None
            or item.source_ref is None
            or item._authority.source_record_hash != item.source_ref.record_hash
            or item._authority.identity_hash is None
            for item in series
        ):
            continue
        for item in series:
            item._verify_source_binding()
        if len(series) < 100:
            continue
        dates = [item.trading_date for item in series]
        if any(
            date not in session_by_date
            or session_by_date[date].day_kind is MarketDayKind.CLOSED
            or session_by_date[date].regular_session is None
            for date in dates
        ):
            continue
        if any(date.value >= as_of.value.date() for date in dates):
            continue
        if any(item.available_at.value > as_of.value for item in series):
            continue
        if [d.value for d in dates] != sorted(d.value for d in dates):
            continue
        if len(set(d.value for d in dates)) != len(dates):
            continue
        # The manifest window is the most recent 126 sessions, but a security
        # needs only 100 finite observations inside that window.  Do not
        # require an observation for every session: that would silently turn
        # the approved 100-observation minimum into a 126-observation minimum.
        window_dates = frozenset(
            _latest_open_session_dates(
                session_by_date,
                cutoff_date=as_of.value.date(),
                count=_CLUSTER_SESSION_WINDOW,
            )
        )
        if len(window_dates) != _CLUSTER_SESSION_WINDOW:
            continue
        window_items = tuple(item for item in series if item.trading_date in window_dates)
        if len(window_items) < 100:
            continue
        dates = [item.trading_date for item in window_items]
        values = [item.value for item in window_items]
        refs = tuple(
            sorted(
                {item.source_ref for item in series if item.source_ref is not None},
                key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash),
            )
        )
        source_refs_by_security[sid] = refs
        return_series[sid] = values
        session_dates[sid] = dates

    # A zero-variance node has no defined Pearson denominator.  Mark it
    # unknown before pair processing so a singleton cannot bypass the gate.
    node_unknown: set[str] = set()
    for sid, values in return_series.items():
        with localcontext(_SCREENING_DECIMAL_CONTEXT):
            mean = sum(values, Decimal(0)) / len(values)
            variance = sum(((value - mean) ** 2 for value in values), Decimal(0))
        if variance <= 0 or not variance.is_finite():
            node_unknown.add(sid)

    # Undirected edges on nodes with complete, non-degenerate data.
    complete_ids = tuple(
        node.value
        for node in ordered_nodes
        if node.value in return_series and node.value not in node_unknown
    )
    # Pair coverage is evaluated only between series that each already met the
    # per-security 100-observation minimum.  A node that failed that minimum is
    # UNKNOWN itself and never renders a complete node UNKNOWN: one sparse
    # holding must not poison the correlation of the whole node set.
    edges: set[tuple[str, str]] = set()
    pair_unknown: set[str] = set()
    for i, first in enumerate(complete_ids):
        for second in complete_ids[i + 1 :]:
            common = sorted(
                set(session_dates[first]) & set(session_dates[second]),
                key=lambda d: d.value,
            )
            if len(common) < 100:
                pair_unknown.update((first, second))
                continue
            by_date_first = {
                d.value: r for d, r in zip(session_dates[first], return_series[first], strict=True)
            }
            by_date_second = {
                d.value: r
                for d, r in zip(session_dates[second], return_series[second], strict=True)
            }
            ri = [by_date_first[d.value] for d in common]
            rj = [by_date_second[d.value] for d in common]
            rho = _pearson(ri, rj)
            if rho is None:
                pair_unknown.update((first, second))
                continue
            if _pearson_meets_threshold(ri, rj, Decimal("0.75")):
                edges.add((first, second))

    unknown = tuple(
        node
        for node in ordered_nodes
        if (
            node.value not in return_series
            or node.value in node_unknown
            or node.value in pair_unknown
        )
    )
    complete_ids = tuple(
        node.value
        for node in ordered_nodes
        if (
            node.value in return_series
            and node.value not in node_unknown
            and node.value not in pair_unknown
        )
    )
    edges = {edge for edge in edges if edge[0] not in pair_unknown and edge[1] not in pair_unknown}

    # Connected components over ordered nodes.
    parent: dict[str, str] = {sid: sid for sid in complete_ids}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a: str, b: str) -> None:
        root_a = find(a)
        root_b = find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for first, second in edges:
        union(first, second)

    components: dict[str, list[str]] = {}
    for sid in complete_ids:
        components.setdefault(find(sid), []).append(sid)
    ordered_components = [
        tuple(by_id[sid] for sid in sorted(members)) for members in components.values()
    ]
    ordered_components.sort(key=lambda members: members[0].value)

    results: list[ClusterResult] = []
    for members in ordered_components:
        component_refs = tuple(
            sorted(
                {
                    ref
                    for member in members
                    for ref in source_refs_by_security.get(member.value, ())
                },
                key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash),
            )
        )
        cluster_id = _cluster_id_for(
            policy_hash=policy_hash,
            as_of=as_of,
            members=members,
        )
        results.append(
            _finalize_cluster_result(
                cluster_id=cluster_id,
                as_of=as_of,
                policy_hash=policy_hash,
                manifest_hash=manifest.manifest_hash,
                members=members,
                status=ClusterStatus.ASSIGNED,
                source_refs=component_refs,
            )
        )
    for node in unknown:
        node_refs = source_refs_by_security.get(node.value, ())
        cluster_id = _cluster_id_for(
            policy_hash=policy_hash,
            as_of=as_of,
            members=(node,),
        )
        results.append(
            _finalize_cluster_result(
                cluster_id=cluster_id,
                as_of=as_of,
                policy_hash=policy_hash,
                manifest_hash=manifest.manifest_hash,
                members=(node,),
                status=ClusterStatus.UNKNOWN,
                source_refs=node_refs,
            )
        )
    return tuple(results)
