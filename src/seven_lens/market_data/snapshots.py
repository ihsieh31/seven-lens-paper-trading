"""Versioned point-in-time market snapshots and the trusted assembler.

A ``MarketSnapshot`` is one immutable, hash-bound observation of a security's
quote, last-bar reference, and 20-session ADV.  The assembler is the only
public build path: it validates feed/entitlement consistency, bid≤ask, quote
age, spread, and market-hours plausibility against the NYSE calendar, and it
derives every computed field (mid, spread bps, 20-session ADV, freshness)
itself.  Callers can never self-report spread, ADV, or freshness.

The minimum ADV computation uses exactly 20 qualifying regular (or half-day)
NYSE sessions with split-aware point-in-time adjustments from confirmed
corporate actions whose ``available_at`` is ≤ cutoff.  Future corporate-action
adjustments are never used.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from datetime import date, time, timedelta
from decimal import Decimal, localcontext
from enum import StrEnum
from fractions import Fraction
from hashlib import sha256
from typing import Final

from seven_lens.clock.market_clock import MarketDayKind, MarketSession
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import (
    MAX_SOURCE_REFS,
    SecurityId,
    SecurityIdentityRecord,
    SecuritySymbol,
    SourceRef,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    validate_lineage,
)
from seven_lens.securities.identity import (
    IdentityQuery,
    IdentityResolutionStatus,
    resolve_identity,
)
from seven_lens.sources.adapters.records import NormalizedSourceRecord, parse_provider_timestamp
from seven_lens.sources.roles import CoverageLabel, P4SourceFamily, SourceRole

_HASH_DOMAIN: Final = b"seven-lens.p4c.market-snapshot.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID_TEXT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_PRODUCER_VERSION: Final = "p4c.market.v1"
_MARKET_SNAPSHOT_AUTHORITY: Final = object()
_MARKET_SNAPSHOT_READBACK_AUTHORITY: Final = object()

QUOTE_MAX_AGE_SECONDS: Final = 5
MAX_SPREAD_BPS: Final = 30
ADV_REQUIRED_SESSIONS: Final = 20
MIN_PRICE: Final = Decimal("5.00")
MIN_ADV20_USD: Final = Decimal("20_000_000")
MIN_TRADING_HISTORY_SESSIONS: Final = 252

# Resource bounds are part of the closed record contract.  A snapshot may
# carry the 252-session history needed by the universe/factor gates plus an
# explicit exchange calendar, but it must not accept unbounded caller input.
MAX_MARKET_SNAPSHOT_ITEMS: Final = 1024
MAX_MARKET_SNAPSHOT_SPLITS: Final = 64
MAX_MARKET_SNAPSHOT_BYTES: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _QuoteProjectionAuthority:
    """Opaque source-derived values retained by one quote projection."""

    source_ref: SourceRef
    symbol: SecuritySymbol
    feed: Feed
    entitlement: Entitlement
    bid: Decimal
    ask: Decimal
    observed_at: UtcTimestamp
    received_at: UtcTimestamp


def _validate_nyse_sessions(sessions: tuple[MarketSession, ...]) -> None:
    """Validate the explicit NYSE session authority used by P4-C."""
    if type(sessions) is not tuple or any(
        type(session) is not MarketSession for session in sessions
    ):
        raise ValueError("sessions must be a tuple of MarketSession values")
    if len(sessions) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("sessions exceed the market snapshot item bound")
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


def _require_complete_weekday_window(
    sessions: tuple[MarketSession, ...],
    *,
    start: TradingDate,
    end_exclusive: date,
) -> None:
    """Require an explicit calendar record for every weekday in a used window.

    Holidays must therefore be represented as ``CLOSED`` instead of silently
    disappearing from a caller-supplied session list.  Weekends may be omitted.
    """
    if type(end_exclusive) is not date:
        raise ValueError("calendar window end requires a date")
    present = {session.trading_date.value for session in sessions}
    current = start.value
    while current < end_exclusive:
        if current.weekday() < 5 and current not in present:
            raise ValueError("NYSE calendar window must include every weekday explicitly")
        current += timedelta(days=1)


_REGULAR_WINDOW_MAX: Final = timedelta(hours=6, minutes=30)
_SESSION_WINDOW_MIN: Final = timedelta(hours=1)
_OPENS_UTC_EARLIEST: Final = time(hour=12)
_OPENS_UTC_LATEST: Final = time(hour=16)


def validate_nyse_session_window(session: MarketSession) -> None:
    """Reject structurally implausible regular-session window *content*.

    Calendar completeness (every weekday present) is checked separately; this
    guards the hours a window record claims.  NYSE regular sessions open
    9:30 ET -- 13:30 UTC in EDT, 14:30 UTC in EST -- and run at most 6h30m,
    so a record claiming a 24-hour or midnight window is structurally a lie.
    """
    window = session.regular_session
    if window is None:
        return
    opens = window.opens_at.value
    duration = window.closes_at.value - opens
    if not _OPENS_UTC_EARLIEST <= opens.time() <= _OPENS_UTC_LATEST:
        raise ValueError("NYSE session window opens outside the plausible UTC range")
    if not _SESSION_WINDOW_MIN <= duration <= _REGULAR_WINDOW_MAX:
        raise ValueError("NYSE session window duration is implausible")


def _session_wire(session: MarketSession) -> dict[str, object]:
    """Serialize one explicit exchange-calendar record without losing closure state."""
    regular_session = session.regular_session
    return {
        "trading_date": str(session.trading_date),
        "day_kind": session.day_kind.value,
        "opens_at": None if regular_session is None else str(regular_session.opens_at),
        "closes_at": None if regular_session is None else str(regular_session.closes_at),
    }


def _source_ref_wire(ref: SourceRef) -> dict[str, str]:
    """Serialize one typed source reference in deterministic field order."""
    return {
        "record_id": ref.record_id,
        "family": ref.family.value,
        "record_hash": ref.record_hash,
    }


class Feed(StrEnum):
    """The exact data feed that produced the quote or bar.

    ``IEX`` is the only feed accepted for latest quotes in P4-C and always
    carries ``LIMITED_MARKET_COVERAGE``.  ``SIP_DELAYED`` is the usual bar
    feed.  Delayed SIP unavailable may never silently fall back to IEX or
    unsupported feeds.
    """

    IEX = "iex"
    SIP_DELAYED = "sip_delayed"


class Entitlement(StrEnum):
    """The account-plan entitlement for which the feed was served.

    An entitlement mismatch against the actual feed is a ``MARKET_DATA_CONFLICT``.
    """

    IEX = "iex"
    SIP = "sip"


class Coverage(StrEnum):
    """Market-coverage label for one snapshot.

    ``COMPLETE`` is never used for P4-C quotes (which are always IEX-limited);
    it is reserved for future SIP-backed quotes.
    """

    COMPLETE = "COMPLETE"
    LIMITED_MARKET_COVERAGE = "LIMITED_MARKET_COVERAGE"


class Freshness(StrEnum):
    """Quote-level freshness; derived from age, never self-reported.

    ``FRESH`` means age ≤ 5 s.  ``STALE`` means age > 5 s.  ``MISSING`` means
    no quote was available.  ``CONFLICT`` means the quote is future,
    out-of-order, or on a closed market day.
    """

    FRESH = "FRESH"
    STALE = "STALE"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class _BarProjectionAuthority:
    """Opaque binding from one daily bar to its source and stable identity."""

    fingerprint: tuple[object, ...]
    source_record_hash: str
    identity_hash: str


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One point-in-time bar used for ADV computation.

    ``trading_date`` is the session date; ``close`` and ``volume`` are the
    bar's regular-session values.  ``source_ref`` ties the bar to the
    accepted P4-A record.
    """

    trading_date: TradingDate
    close: Decimal
    volume: int
    source_ref: SourceRef
    feed: Feed
    available_at: UtcTimestamp
    security_id: SecurityId
    _authority: _BarProjectionAuthority | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _BarProjectionAuthority:
            raise ValueError("daily bars must be produced by the historical-record factory")
        if type(self.trading_date) is not TradingDate:
            raise ValueError("trading_date requires an exact TradingDate")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.close) is not Decimal or not self.close.is_finite() or self.close <= 0:
            raise ValueError("close must be a positive finite Decimal")
        if type(self.volume) is not int or self.volume < 0:
            raise ValueError("volume must be a non-negative integer")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if type(self.feed) is not Feed:
            raise ValueError("feed requires an exact Feed")
        if (
            self.feed is not Feed.SIP_DELAYED
            or self.source_ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS
        ):
            raise ValueError("daily bars require the delayed SIP historical-bars source")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if self._authority.fingerprint != _daily_bar_fingerprint(self):
            raise ValueError("daily bar authority is not bound to frozen content")


def _daily_bar_fingerprint(value: DailyBar) -> tuple[object, ...]:
    return (
        value.trading_date,
        value.close,
        value.volume,
        value.source_ref,
        value.feed,
        value.available_at,
        value.security_id,
    )


def daily_bars_from_record(
    record: NormalizedSourceRecord,
    *,
    security_id: SecurityId,
    identities: tuple[SecurityIdentityRecord, ...],
    known_at: UtcTimestamp,
) -> tuple[DailyBar, ...]:
    """Project exact 1Day delayed-SIP bars through point-in-time P4-B identity."""
    if type(record) is not NormalizedSourceRecord:
        raise ValueError("bar source requires an exact NormalizedSourceRecord")
    record.verify_integrity()
    if (
        record.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS
        or record.endpoint_id != "stock_bars"
    ):
        raise ValueError("daily bars require the historical-bars source endpoint")
    if type(security_id) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    if type(identities) is not tuple or any(
        type(identity) is not SecurityIdentityRecord for identity in identities
    ):
        raise ValueError("identities must be a tuple of SecurityIdentityRecord values")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    for identity in identities:
        identity.verify_integrity()
    available_at = record.available_at
    if type(available_at) is not UtcTimestamp or available_at.value > known_at.value:
        raise ValueError("historical-bars source is not available by known_at")
    payload = record.payload.to_dict()
    if set(payload) != {"symbol", "feed", "timeframe", "bars", "next_page_token"}:
        raise ValueError("historical-bars payload has an unexpected shape")
    if payload.get("feed") != "sip" or payload.get("timeframe") != "1Day":
        raise ValueError("daily bars require the exact delayed-SIP 1Day request")
    symbol = SecuritySymbol(str(payload.get("symbol")))
    raw_bars = payload.get("bars")
    if type(raw_bars) is not list or len(raw_bars) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("historical-bars payload is missing or exceeds the item bound")
    source_ref = SourceRef(record.record_id, record.family, record.record_hash)
    projected: list[tuple[UtcTimestamp, DailyBar]] = []
    for raw in raw_bars:
        if type(raw) is not dict or set(raw) != {"t", "o", "h", "l", "c", "v"}:
            raise ValueError("historical bar has an unexpected shape")
        timestamp = parse_provider_timestamp(str(raw.get("t")))
        if timestamp.value > known_at.value:
            raise ValueError("historical bar is after known_at")
        resolution = resolve_identity(
            identities,
            IdentityQuery(
                as_of=timestamp,
                known_at=known_at,
                security_id=security_id,
                symbol=symbol,
            ),
        )
        if (
            resolution.status is not IdentityResolutionStatus.RESOLVED
            or resolution.record is None
            or resolution.record.symbol != symbol
        ):
            raise ValueError("historical bar does not resolve to one stable identity")
        close_raw = raw.get("c")
        volume_raw = raw.get("v")
        if type(close_raw) is not str or type(volume_raw) is not int:
            raise ValueError("historical close/volume types are invalid")
        body: dict[str, object] = {
            "trading_date": TradingDate(timestamp.value.date()),
            "close": Decimal(close_raw),
            "volume": volume_raw,
            "source_ref": source_ref,
            "feed": Feed.SIP_DELAYED,
            "available_at": available_at,
            "security_id": security_id,
        }
        provisional = object.__new__(DailyBar)
        for name, value in body.items():
            object.__setattr__(provisional, name, value)
        authority = _BarProjectionAuthority(
            fingerprint=_daily_bar_fingerprint(provisional),
            source_record_hash=record.record_hash,
            identity_hash=resolution.record.identity_hash,
        )
        projected.append(
            (timestamp, DailyBar(**body, _authority=authority))  # type: ignore[arg-type]
        )
    timestamps = [timestamp.value for timestamp, _ in projected]
    if timestamps != sorted(timestamps) or len(timestamps) != len(set(timestamps)):
        raise ValueError("historical bars must be ordered and unique")
    return tuple(bar for _, bar in projected)


@dataclass(frozen=True, slots=True)
class QuoteInput:
    """One source-derived IEX quote projection.

    The public constructor is intentionally not an ingestion seam: callers
    must use :func:`quote_input_from_record`, which verifies the immutable
    P4-A record and derives every field from its canonical payload.
    """

    source_ref: SourceRef
    symbol: SecuritySymbol
    feed: Feed
    entitlement: Entitlement
    bid: Decimal
    ask: Decimal
    observed_at: UtcTimestamp
    received_at: UtcTimestamp
    _authority: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _QuoteProjectionAuthority:
            raise ValueError("QuoteInput must be produced by the source-record factory")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.feed) is not Feed:
            raise ValueError("feed requires an exact Feed")
        if type(self.entitlement) is not Entitlement:
            raise ValueError("entitlement requires an exact Entitlement")
        if type(self.bid) is not Decimal or not self.bid.is_finite() or self.bid <= 0:
            raise ValueError("bid must be a positive finite Decimal")
        if type(self.ask) is not Decimal or not self.ask.is_finite() or self.ask <= 0:
            raise ValueError("ask must be a positive finite Decimal")
        if self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        if type(self.observed_at) is not UtcTimestamp:
            raise ValueError("observed_at requires canonical UTC")
        if type(self.received_at) is not UtcTimestamp:
            raise ValueError("received_at requires canonical UTC")
        self._verify_source_binding()

    def _verify_source_binding(self) -> None:
        """Re-check the opaque authority after any attempted mutation."""
        authority = self._authority
        assert type(authority) is _QuoteProjectionAuthority
        if (
            authority.source_ref != self.source_ref
            or authority.symbol != self.symbol
            or authority.feed is not self.feed
            or authority.entitlement is not self.entitlement
            or authority.bid != self.bid
            or authority.ask != self.ask
            or authority.observed_at != self.observed_at
            or authority.received_at != self.received_at
        ):
            raise ValueError("QuoteInput is not bound to its source record")


_SOURCE_DECIMAL_TEXT: Final = re.compile(r"^\d{1,12}(\.\d{1,8})?$")


def _source_record_ref(
    record: NormalizedSourceRecord,
    *,
    family: P4SourceFamily,
    endpoint_id: str,
) -> SourceRef:
    """Verify one accepted P4-A record before deriving a typed projection."""
    if type(record) is not NormalizedSourceRecord:
        raise ValueError("source record requires an exact NormalizedSourceRecord")
    record.verify_integrity()
    if record.family is not family or record.endpoint_id != endpoint_id:
        raise ValueError("source record family or endpoint is not approved")
    if record.role is not SourceRole.AUTHORITY:
        raise ValueError("source record must be an authority record")
    return SourceRef(record.record_id, record.family, record.record_hash)


def _source_decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not str or _SOURCE_DECIMAL_TEXT.fullmatch(value) is None:
        raise ValueError(f"source payload {field_name} must be bounded decimal text")
    value_decimal = Decimal(value)
    if not value_decimal.is_finite() or value_decimal <= 0:
        raise ValueError(f"source payload {field_name} must be positive and finite")
    return value_decimal


def quote_input_from_record(record: NormalizedSourceRecord) -> QuoteInput:
    """Derive an immutable quote projection from one accepted P4-A IEX record.

    The source payload is the sole authority for symbol, feed, prices, and
    observation time.  The record's availability (or retrieval time when the
    adapter omitted an explicit availability stamp) is the received time.
    """
    source_ref = _source_record_ref(
        record, family=P4SourceFamily.ALPACA_IEX_QUOTES, endpoint_id="latest_quote"
    )
    if record.coverage is not CoverageLabel.LIMITED_MARKET_COVERAGE or record.coverage_warning != (
        "IEX feed only; not full NBBO/SIP market coverage"
    ):
        raise ValueError("IEX source record lacks the approved limited-coverage warning")
    payload = record.payload.to_dict()
    required = {"symbol", "bid_price", "ask_price", "timestamp", "feed"}
    allowed = required | {"bid_size", "ask_size"}
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise ValueError("IEX source payload has an unexpected shape")
    symbol_value = payload.get("symbol")
    if type(symbol_value) is not str:
        raise ValueError("IEX source payload symbol must be text")
    symbol = SecuritySymbol(symbol_value)
    if payload.get("feed") != "iex":
        raise ValueError("IEX source payload feed must be iex")
    bid = _source_decimal(payload.get("bid_price"), "bid_price")
    ask = _source_decimal(payload.get("ask_price"), "ask_price")
    if bid > ask:
        raise ValueError("IEX source payload bid must not exceed ask")
    timestamp = payload.get("timestamp")
    if type(timestamp) is not str:
        raise ValueError("IEX source payload timestamp must be text")
    try:
        observed_at = parse_provider_timestamp(timestamp)
    except ValueError as error:
        raise ValueError("IEX source payload timestamp is invalid") from error
    if record.observation_at is None or record.observation_at != observed_at:
        raise ValueError("IEX source observation_at is not bound to its payload timestamp")
    received_at = record.available_at or record.retrieved_at
    if received_at.value < observed_at.value:
        raise ValueError("IEX source availability precedes its observation")
    authority = _QuoteProjectionAuthority(
        source_ref=source_ref,
        symbol=symbol,
        feed=Feed.IEX,
        entitlement=Entitlement.IEX,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        received_at=received_at,
    )
    return QuoteInput(
        source_ref=source_ref,
        symbol=symbol,
        feed=Feed.IEX,
        entitlement=Entitlement.IEX,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        received_at=received_at,
        _authority=authority,
    )


@dataclass(frozen=True, slots=True)
class _SplitAdjustmentAuthority:
    """Private capability binding one adjustment to a validated P4-B event."""

    event_id: str
    event_record_hash: str
    security_id: SecurityId
    security_identity_hash: str
    action_type: CorporateActionType
    state: CorporateActionState
    numerator: int
    denominator: int
    ex_date: TradingDate
    effective_date: TradingDate
    available_at: UtcTimestamp
    source_ref: SourceRef
    source_refs: tuple[SourceRef, ...]


@dataclass(frozen=True, slots=True)
class SplitAdjustment:
    """One confirmed split whose adjustment applies to bars before ``ex_date``.

    The public object is derived from a validated P4-B corporate-action
    lineage; the legacy ``source_ref`` field remains the canonical Alpaca
    detection ref while ``source_refs`` exposes the complete event closure.

    ``adjusted_close = close * (denominator / numerator)``
    ``adjusted_volume = volume * (numerator / denominator)``
    """

    security_id: SecurityId
    ex_date: TradingDate
    numerator: int
    denominator: int
    source_ref: SourceRef
    available_at: UtcTimestamp
    confirmed: bool
    _authority: _SplitAdjustmentAuthority | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _SplitAdjustmentAuthority:
            raise ValueError(
                "SplitAdjustment must be produced by a validated corporate-action lineage"
            )
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.ex_date) is not TradingDate:
            raise ValueError("ex_date requires an exact TradingDate")
        if type(self.numerator) is not int or self.numerator <= 0:
            raise ValueError("numerator must be a positive integer")
        if type(self.denominator) is not int or self.denominator <= 0:
            raise ValueError("denominator must be a positive integer")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if self.source_ref.family is not P4SourceFamily.ALPACA_CORPORATE_ACTIONS:
            raise ValueError("split adjustments require the corporate-actions authority")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        if type(self.confirmed) is not bool or not self.confirmed:
            raise ValueError("split adjustments require an explicitly confirmed action")
        self._verify_source_binding()

    @property
    def event_id(self) -> str:
        """Return the bound P4-B corporate-action event identifier."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.event_id

    @property
    def event_record_hash(self) -> str:
        """Return the bound P4-B head hash for audit/readback joins."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.event_record_hash

    @property
    def security_identity_hash(self) -> str:
        """Return the exact P4-B identity version bound to the event head."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.security_identity_hash

    @property
    def action_type(self) -> CorporateActionType:
        """Return the split type bound by the P4-B event head."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.action_type

    @property
    def effective_date(self) -> TradingDate:
        """Return the effective date bound by the P4-B event head."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.effective_date

    @property
    def source_refs(self) -> tuple[SourceRef, ...]:
        """Return every source reference carried by the validated event head."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError("SplitAdjustment has no validated corporate-action authority")
        return authority.source_refs

    def _verify_source_binding(self) -> None:
        """Re-check the opaque event authority after an attempted mutation."""
        authority = self._authority
        if type(authority) is not _SplitAdjustmentAuthority:
            raise ValueError(
                "SplitAdjustment must be produced by a validated corporate-action lineage"
            )
        if (
            authority.security_id != self.security_id
            or _HASH_TEXT.fullmatch(authority.security_identity_hash) is None
            or authority.numerator != self.numerator
            or authority.denominator != self.denominator
            or authority.ex_date != self.ex_date
            or authority.available_at != self.available_at
            or authority.source_ref != self.source_ref
            or authority.state is not CorporateActionState.CONFIRMED
            or not self.confirmed
            or authority.action_type
            not in (CorporateActionType.FORWARD_SPLIT, CorporateActionType.REVERSE_SPLIT)
            or authority.source_ref not in authority.source_refs
        ):
            raise ValueError("SplitAdjustment is not bound to its validated corporate-action event")


def split_adjustment_from_lineage(
    lineage: tuple[CorporateActionRecord, ...],
) -> SplitAdjustment:
    """Derive one split adjustment from a fully validated P4-B event lineage.

    A self-hashed ``CorporateActionRecord`` is not sufficient: the lineage
    must begin at ``DETECTED`` and reach exactly ``CONFIRMED`` through the
    append-only transition table.  All adjustment fields are copied from the
    validated head; the opaque authority retains the full source closure and
    effective date and complete source closure; the wire retains the legacy
    selected source ref alongside those complete binding fields.
    """
    if type(lineage) is not tuple:
        raise ValueError("corporate-action lineage requires a tuple")
    head = validate_lineage(lineage)
    if head.state is not CorporateActionState.CONFIRMED:
        raise ValueError("split adjustments require a CONFIRMED corporate-action head")
    if head.action_type not in (
        CorporateActionType.FORWARD_SPLIT,
        CorporateActionType.REVERSE_SPLIT,
    ):
        raise ValueError("split adjustments require a supported split action")
    alpaca_refs = tuple(
        ref for ref in head.source_refs if ref.family is P4SourceFamily.ALPACA_CORPORATE_ACTIONS
    )
    if not alpaca_refs:
        raise ValueError("confirmed split lineage requires an Alpaca corporate-action source ref")
    source_ref = min(alpaca_refs, key=lambda ref: (ref.record_id, ref.record_hash))
    authority = _SplitAdjustmentAuthority(
        event_id=head.event_id,
        event_record_hash=head.record_hash,
        security_id=head.security_id,
        security_identity_hash=head.security_identity_hash,
        action_type=head.action_type,
        state=head.state,
        numerator=head.ratio.numerator,
        denominator=head.ratio.denominator,
        ex_date=head.ex_date,
        effective_date=head.effective_date,
        available_at=head.available_at,
        source_ref=source_ref,
        source_refs=head.source_refs,
    )
    return SplitAdjustment(
        security_id=head.security_id,
        ex_date=head.ex_date,
        numerator=head.ratio.numerator,
        denominator=head.ratio.denominator,
        source_ref=source_ref,
        available_at=head.available_at,
        confirmed=True,
        _authority=authority,
    )


def _reconstruct_split_adjustment(
    *,
    authority: object,
    event_id: str,
    event_record_hash: str,
    security_identity_hash: str,
    action_type: CorporateActionType,
    security_id: SecurityId,
    ex_date: TradingDate,
    effective_date: TradingDate,
    numerator: int,
    denominator: int,
    source_ref: SourceRef,
    source_refs: tuple[SourceRef, ...],
    available_at: UtcTimestamp,
    confirmed: bool,
) -> SplitAdjustment:
    """Rebuild a split only after the persistence adapter verified its P4-B head.

    This is intentionally guarded by the same private capability as market
    snapshot readback.  Public callers must use ``split_adjustment_from_lineage``.
    """
    if authority is not _MARKET_SNAPSHOT_READBACK_AUTHORITY:
        raise ValueError("split adjustment reconstruction requires trusted readback authority")
    split_authority = _SplitAdjustmentAuthority(
        event_id=event_id,
        event_record_hash=event_record_hash,
        security_id=security_id,
        security_identity_hash=security_identity_hash,
        action_type=action_type,
        state=CorporateActionState.CONFIRMED,
        numerator=numerator,
        denominator=denominator,
        ex_date=ex_date,
        effective_date=effective_date,
        available_at=available_at,
        source_ref=source_ref,
        source_refs=source_refs,
    )
    return SplitAdjustment(
        security_id=security_id,
        ex_date=ex_date,
        numerator=numerator,
        denominator=denominator,
        source_ref=source_ref,
        available_at=available_at,
        confirmed=confirmed,
        _authority=split_authority,
    )


def _spread_exact(bid: Decimal, ask: Decimal) -> tuple[int, bool]:
    """Return the exact floor spread bps and the exact ``> MAX_SPREAD_BPS`` flag.

    The spread quotient ``(ask - bid) * 20000 / (bid + ask)`` is evaluated in
    unbounded rational arithmetic: no context-precision rounding may move a
    boundary decision, so the derived integer and the flag agree with the
    migration's exact interval check by pure algebra.
    """
    numerator = (Fraction(ask) - Fraction(bid)) * 20000
    denominator = Fraction(bid) + Fraction(ask)
    return int(numerator // denominator), numerator > MAX_SPREAD_BPS * denominator


def _mid_exact(bid: Decimal, ask: Decimal) -> Decimal:
    """Compute the exact finite-decimal midpoint independent of global precision.

    ``Decimal`` division normally uses the process context.  A low-precision
    caller context could therefore round a midpoint that has a finite exact
    representation (division by two), creating a wire that does not match the
    source quote.  A local precision sized from the operands keeps addition and
    division exact without mutating the caller's context.
    """
    precision = max(len(bid.as_tuple().digits), len(ask.as_tuple().digits)) + 2
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        return (bid + ask) / Decimal(2)


def _canonical_reasons(reasons: tuple[ClosedReason, ...]) -> tuple[ClosedReason, ...]:
    if not reasons:
        return reasons
    order = {reason: index for index, reason in enumerate(ClosedReason)}
    return tuple(sorted(reasons, key=order.__getitem__))


def _validate_market_snapshot_fields(values: dict[str, object]) -> None:
    """Fail closed on any field that violates the market-snapshot contract."""
    if type(values.get("security_id")) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    if type(values.get("symbol")) is not SecuritySymbol:
        raise ValueError("symbol requires an exact SecuritySymbol")
    if type(values.get("as_of")) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    if type(values.get("known_at")) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    if type(values.get("received_at")) is not UtcTimestamp:
        raise ValueError("received_at requires canonical UTC")
    if type(values.get("observed_at")) is not UtcTimestamp:
        raise ValueError("observed_at requires canonical UTC")
    if type(values.get("feed")) is not Feed:
        raise ValueError("feed requires an exact Feed")
    if type(values.get("entitlement")) is not Entitlement:
        raise ValueError("entitlement requires an exact Entitlement")
    if values["feed"] is not Feed.IEX or values["entitlement"] is not Entitlement.IEX:
        raise ValueError("market snapshots require the IEX feed and entitlement")
    bid = values.get("bid")
    if type(bid) is not Decimal or not bid.is_finite() or bid <= 0:
        raise ValueError("bid must be a positive finite Decimal")
    ask = values.get("ask")
    if type(ask) is not Decimal or not ask.is_finite() or ask <= 0:
        raise ValueError("ask must be a positive finite Decimal")
    if bid > ask:
        raise ValueError("bid must not exceed ask")
    mid = values.get("mid")
    if type(mid) is not Decimal or not mid.is_finite() or mid <= 0:
        raise ValueError("mid must be a positive finite Decimal")
    expected_mid = _mid_exact(bid, ask)
    if mid != expected_mid:
        raise ValueError("mid must be derived from bid and ask")
    spread_bps = values.get("spread_bps")
    if type(spread_bps) is not int or spread_bps < 0:
        raise ValueError("spread_bps must be a non-negative integer")
    exact_spread_bps, exact_spread_too_wide = _spread_exact(bid, ask)
    if spread_bps != exact_spread_bps:
        raise ValueError("spread_bps must be derived from bid and ask")
    for name in ("last", "adv20_usd"):
        value = values.get(name)
        if value is not None and (
            type(value) is not Decimal or not value.is_finite() or value <= 0
        ):
            raise ValueError(f"{name} must be a positive finite Decimal or None")
    last = values.get("last")
    if last is None or last != expected_mid:
        raise ValueError("last must be the source-derived quote mid")
    bar_feed = values.get("bar_feed")
    if bar_feed is not None and type(bar_feed) is not Feed:
        raise ValueError("bar_feed requires an exact Feed or None")
    if bar_feed is not None and bar_feed is not Feed.SIP_DELAYED:
        raise ValueError("market snapshot bars require the delayed SIP feed")
    bar_refs = values.get("bar_refs")
    if type(bar_refs) is not tuple:
        raise ValueError("bar_refs must be a tuple of SourceRef values")
    if len(bar_refs) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("bar_refs exceed the market snapshot item bound")
    if any(type(ref) is not SourceRef for ref in bar_refs):
        raise ValueError("bar_refs must be a tuple of SourceRef values")
    if tuple(ref.record_id for ref in bar_refs) != tuple(
        sorted({ref.record_id for ref in bar_refs})
    ):
        raise ValueError("bar_refs must be sorted and unique")
    if any(ref.family is not P4SourceFamily.ALPACA_HISTORICAL_BARS for ref in bar_refs):
        raise ValueError("bar_refs require the historical-bars source family")
    bar_dates = values.get("bar_dates")
    if type(bar_dates) is not tuple or any(type(date) is not TradingDate for date in bar_dates):
        raise ValueError("bar_dates must be a tuple of TradingDate values")
    if len(bar_dates) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("bar_dates exceed the market snapshot item bound")
    if bar_dates != tuple(sorted(set(bar_dates), key=lambda date: date.value)):
        raise ValueError("bar_dates must be sorted and unique")
    if bool(bar_dates) != bool(bar_refs):
        raise ValueError("bar_dates and bar_refs must be present together")
    split_adjustment_refs = values.get("split_adjustment_refs")
    if type(split_adjustment_refs) is not tuple or any(
        type(ref) is not SourceRef for ref in split_adjustment_refs
    ):
        raise ValueError("split_adjustment_refs must be a tuple of SourceRef values")
    if len(split_adjustment_refs) > MAX_MARKET_SNAPSHOT_SPLITS:
        raise ValueError("split_adjustment_refs exceed the market snapshot item bound")
    if split_adjustment_refs != tuple(
        sorted(set(split_adjustment_refs), key=lambda ref: (ref.record_id, ref.family.value))
    ):
        raise ValueError("split_adjustment_refs must be sorted and unique")
    if any(
        ref.family is not P4SourceFamily.ALPACA_CORPORATE_ACTIONS for ref in split_adjustment_refs
    ):
        raise ValueError("split_adjustment_refs require the corporate-actions source family")
    split_adjustments = values.get("split_adjustments")
    if type(split_adjustments) is not tuple or any(
        type(adjustment) is not SplitAdjustment for adjustment in split_adjustments
    ):
        raise ValueError("split_adjustments must be a tuple of SplitAdjustment values")
    for adjustment in split_adjustments:
        adjustment._verify_source_binding()
        if _EVENT_ID_TEXT.fullmatch(adjustment.event_id) is None:
            raise ValueError("split adjustment event_id is not canonical")
        if _HASH_TEXT.fullmatch(adjustment.event_record_hash) is None:
            raise ValueError("split adjustment event_record_hash must be a SHA-256 digest")
        if _HASH_TEXT.fullmatch(adjustment.security_identity_hash) is None:
            raise ValueError("split adjustment identity hash must be a SHA-256 digest")
        if type(adjustment.effective_date) is not TradingDate:
            raise ValueError("split adjustment effective_date requires an exact TradingDate")
        if adjustment.effective_date.value < adjustment.ex_date.value:
            raise ValueError("split adjustment effective_date cannot precede ex_date")
        source_refs = adjustment.source_refs
        if (
            type(source_refs) is not tuple
            or not source_refs
            or len(source_refs) > MAX_SOURCE_REFS
            or any(type(ref) is not SourceRef for ref in source_refs)
        ):
            raise ValueError("split adjustment source_refs are invalid or exceed their bound")
        if len({ref.record_id for ref in source_refs}) != len(source_refs):
            raise ValueError("split adjustment source_refs must be unique")
        if adjustment.source_ref not in source_refs:
            raise ValueError("split adjustment source_ref must be in source_refs")
    if len(split_adjustments) > MAX_MARKET_SNAPSHOT_SPLITS:
        raise ValueError("split_adjustments exceed the market snapshot item bound")
    if tuple(
        (
            adjustment.ex_date.value,
            adjustment.source_ref.record_id,
            adjustment.source_ref.record_hash,
        )
        for adjustment in split_adjustments
    ) != tuple(
        sorted(
            (
                adjustment.ex_date.value,
                adjustment.source_ref.record_id,
                adjustment.source_ref.record_hash,
            )
            for adjustment in split_adjustments
        )
    ):
        raise ValueError("split_adjustments must be ordered by ex-date and source")
    if len({adjustment.ex_date for adjustment in split_adjustments}) != len(split_adjustments):
        raise ValueError("split_adjustments must not repeat an ex-date")
    if any(adjustment.security_id != values.get("security_id") for adjustment in split_adjustments):
        raise ValueError("split adjustments must bind to the snapshot security")
    derived_split_refs = tuple(
        sorted(
            {adjustment.source_ref for adjustment in split_adjustments},
            key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
        )
    )
    if split_adjustment_refs != derived_split_refs:
        raise ValueError("split_adjustment_refs must be derived from split_adjustments")
    if type(values.get("quote_source_ref")) is not SourceRef:
        raise ValueError("quote_source_ref requires an exact SourceRef")
    if type(values.get("coverage")) is not Coverage:
        raise ValueError("coverage requires an exact Coverage")
    if type(values.get("freshness")) is not Freshness:
        raise ValueError("freshness requires an exact Freshness")
    if values["freshness"] is Freshness.CONFLICT:
        raise ValueError("conflicting market data has no snapshot authority")
    if values.get("feed") is Feed.IEX and values.get("coverage") is not (
        Coverage.LIMITED_MARKET_COVERAGE
    ):
        raise ValueError("IEX quotes must carry LIMITED_MARKET_COVERAGE")
    if values.get("feed") is Feed.IEX and values.get("coverage_warning") != (
        "IEX limited market coverage"
    ):
        raise ValueError("IEX quotes require the exact limited-coverage warning")
    coverage_warning = values.get("coverage_warning")
    if coverage_warning is not None and type(coverage_warning) is not str:
        raise ValueError("coverage_warning must be a string or None")
    reasons = values.get("reasons", ())
    if type(reasons) is not tuple or any(type(r) is not ClosedReason for r in reasons):
        raise ValueError("reasons require exact ClosedReason values")
    if len(set(reasons)) != len(reasons):
        raise ValueError("reasons must be unique")
    if reasons != _canonical_reasons(reasons):
        raise ValueError("reasons must use the canonical order")
    if exact_spread_too_wide != (ClosedReason.SPREAD_TOO_WIDE in reasons):
        raise ValueError("spread reason does not match the exact spread")
    if type(values.get("producer_version")) is not str or not values.get("producer_version"):
        raise ValueError("producer_version requires non-empty bounded text")
    if type(values.get("schema_version")) is not SchemaVersion:
        raise ValueError("schema_version requires an exact SchemaVersion")
    as_of = values["as_of"]
    known_at = values["known_at"]
    received_at = values["received_at"]
    observed_at = values["observed_at"]
    assert type(as_of) is UtcTimestamp
    assert type(known_at) is UtcTimestamp
    assert type(received_at) is UtcTimestamp
    assert type(observed_at) is UtcTimestamp
    sessions = values.get("sessions")
    if type(sessions) is not tuple:
        raise ValueError("sessions must be a tuple of MarketSession values")
    _validate_nyse_sessions(sessions)
    session_by_date = {session.trading_date: session for session in sessions}
    as_of_session = session_by_date.get(TradingDate(as_of.value.date()))
    if (
        as_of_session is None
        or as_of_session.day_kind is MarketDayKind.CLOSED
        or as_of_session.regular_session is None
        or not as_of_session.regular_session.contains(as_of)
    ):
        raise ValueError("as_of must bind to an explicit open NYSE session")
    observed_session = session_by_date.get(TradingDate(observed_at.value.date()))
    if (
        observed_session is None
        or observed_session.day_kind is MarketDayKind.CLOSED
        or observed_session.regular_session is None
        or not observed_session.regular_session.contains(observed_at)
    ):
        raise ValueError("observed_at must bind to an explicit open NYSE session")
    if any(
        date not in session_by_date
        or session_by_date[date].day_kind is MarketDayKind.CLOSED
        or session_by_date[date].regular_session is None
        for date in bar_dates
    ):
        raise ValueError("bar_dates must bind to explicit open NYSE sessions")
    if bar_dates:
        # The whole bar-history span must be explicitly calendared: a dense
        # recent window must not mask a thin historic calendar whose "sessions"
        # are spaced weeks apart, because the 252-session trading-history gate
        # counts bar dates that this calendar blesses.
        _require_complete_weekday_window(
            sessions,
            start=bar_dates[0],
            end_exclusive=as_of.value.date(),
        )
    if known_at.value > as_of.value:
        raise ValueError("known_at cannot be after as_of")
    if observed_at.value > known_at.value:
        raise ValueError("observed_at cannot be after known_at")
    if received_at.value > known_at.value:
        raise ValueError("received_at cannot be after known_at")
    if received_at.value > as_of.value:
        raise ValueError("received_at cannot be after as_of")
    if observed_at.value > as_of.value:
        raise ValueError("observed_at cannot be after as_of")
    if received_at.value < observed_at.value:
        raise ValueError("received_at cannot be before observed_at")
    if any(adjustment.available_at.value > known_at.value for adjustment in split_adjustments):
        raise ValueError("split adjustments must be visible at known_at")
    if values["freshness"] is Freshness.MISSING:
        raise ValueError("a market snapshot with quote fields cannot be MISSING")
    age = as_of.value - observed_at.value
    expected_freshness = (
        Freshness.FRESH if age <= timedelta(seconds=QUOTE_MAX_AGE_SECONDS) else Freshness.STALE
    )
    if values["freshness"] is not expected_freshness:
        raise ValueError("freshness does not match the observed quote age")
    if (values["freshness"] is Freshness.STALE) != (ClosedReason.QUOTE_MISSING_OR_STALE in reasons):
        raise ValueError("stale reason does not match quote freshness")
    quote_source_ref = values["quote_source_ref"]
    assert type(quote_source_ref) is SourceRef
    if quote_source_ref.family is not P4SourceFamily.ALPACA_IEX_QUOTES:
        raise ValueError("quote source_ref family does not match the IEX feed")


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One versioned, immutable, hash-bound market snapshot.

    Every derived field (mid, spread_bps, adv20_usd, freshness) is computed
    by the trusted assembler; callers may never supply them as pre-computed
    values.  The snapshot carries ``reasons`` for any eligibility findings
    (e.g. stale quote, wide spread) so that downstream stages can carry them
    into the candidate lineage.
    """

    security_id: SecurityId
    symbol: SecuritySymbol
    as_of: UtcTimestamp
    known_at: UtcTimestamp
    observed_at: UtcTimestamp
    received_at: UtcTimestamp
    feed: Feed
    entitlement: Entitlement
    bid: Decimal
    ask: Decimal
    mid: Decimal
    spread_bps: int
    quote_source_ref: SourceRef
    coverage: Coverage
    freshness: Freshness
    producer_version: str
    schema_version: SchemaVersion
    sessions: tuple[MarketSession, ...] = ()
    last: Decimal | None = None
    adv20_usd: Decimal | None = None
    bar_feed: Feed | None = None
    bar_refs: tuple[SourceRef, ...] = ()
    bar_dates: tuple[TradingDate, ...] = ()
    split_adjustment_refs: tuple[SourceRef, ...] = ()
    split_adjustments: tuple[SplitAdjustment, ...] = ()
    coverage_warning: str | None = None
    reasons: tuple[ClosedReason, ...] = ()
    snapshot_hash: str = ""
    _authority: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._authority is not _MARKET_SNAPSHOT_AUTHORITY:
            raise ValueError("MarketSnapshot must be produced by the trusted assembler")
        _validate_market_snapshot_fields(
            {
                field.name: getattr(self, field.name)
                for field in fields(MarketSnapshot)
                if field.name not in ("snapshot_hash", "_authority")
            }
        )
        if type(self.snapshot_hash) is not str or _HASH_TEXT.fullmatch(self.snapshot_hash) is None:
            raise ValueError("snapshot hash must be a SHA-256 digest")
        if self.snapshot_hash != self.compute_hash():
            raise ValueError("snapshot hash does not match frozen content")

    def wire(self) -> dict[str, object]:
        for adjustment in self.split_adjustments:
            adjustment._verify_source_binding()
        wire: dict[str, object] = {
            "security_id": self.security_id.value,
            "symbol": self.symbol.value,
            "as_of": str(self.as_of),
            "known_at": str(self.known_at),
            "observed_at": str(self.observed_at),
            "received_at": str(self.received_at),
            "feed": self.feed.value,
            "entitlement": self.entitlement.value,
            "bid": str(self.bid),
            "ask": str(self.ask),
            "mid": str(self.mid),
            "spread_bps": self.spread_bps,
            "last": None if self.last is None else str(self.last),
            "adv20_usd": None if self.adv20_usd is None else str(self.adv20_usd),
            "bar_feed": None if self.bar_feed is None else self.bar_feed.value,
            "bar_refs": [
                {"record_id": r.record_id, "family": r.family.value, "record_hash": r.record_hash}
                for r in self.bar_refs
            ],
            "bar_dates": [str(date) for date in self.bar_dates],
            "sessions": [_session_wire(session) for session in self.sessions],
            "split_adjustment_refs": [
                {"record_id": r.record_id, "family": r.family.value, "record_hash": r.record_hash}
                for r in self.split_adjustment_refs
            ],
            "split_adjustments": [
                {
                    "security_id": adjustment.security_id.value,
                    "ex_date": str(adjustment.ex_date),
                    "numerator": adjustment.numerator,
                    "denominator": adjustment.denominator,
                    "event_id": adjustment.event_id,
                    "event_record_hash": adjustment.event_record_hash,
                    "security_identity_hash": adjustment.security_identity_hash,
                    "action_type": adjustment.action_type.value,
                    "effective_date": str(adjustment.effective_date),
                    "source_ref": {
                        "record_id": adjustment.source_ref.record_id,
                        "family": adjustment.source_ref.family.value,
                        "record_hash": adjustment.source_ref.record_hash,
                    },
                    "source_refs": [_source_ref_wire(ref) for ref in adjustment.source_refs],
                    "available_at": str(adjustment.available_at),
                    "confirmed": adjustment.confirmed,
                }
                for adjustment in self.split_adjustments
            ],
            "quote_source_ref": {
                "record_id": self.quote_source_ref.record_id,
                "family": self.quote_source_ref.family.value,
                "record_hash": self.quote_source_ref.record_hash,
            },
            "coverage": self.coverage.value,
            "freshness": self.freshness.value,
            "coverage_warning": self.coverage_warning,
            "reasons": [r.value for r in self.reasons],
            "producer_version": self.producer_version,
            "schema_version": str(self.schema_version),
        }
        _canonical_market_wire_bytes(wire)
        return wire

    def compute_hash(self) -> str:
        canonical = _canonical_market_wire_bytes(self.wire())
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.snapshot_hash != self.compute_hash():
            raise ValueError("snapshot hash does not match frozen content")
        return True


def _canonical_market_wire_bytes(wire: dict[str, object]) -> bytes:
    """Serialize the market wire and enforce its UTF-8 resource bound."""
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(canonical) > MAX_MARKET_SNAPSHOT_BYTES:
        raise ValueError("market snapshot canonical wire exceeds the byte bound")
    return canonical


def _field_default(field: object) -> object:
    """Return the default value for a dataclass field, handling MISSING sentinel."""
    from dataclasses import MISSING, Field

    if not isinstance(field, Field):
        return None
    if field.default is not MISSING:
        return field.default
    if field.default_factory is not MISSING:
        return field.default_factory()
    return None


def compute_adv20(
    bars: tuple[DailyBar, ...],
    sessions: tuple[MarketSession, ...],
    split_adjustments: tuple[SplitAdjustment, ...] = (),
    *,
    cutoff: UtcTimestamp | None = None,
    known_at: UtcTimestamp | None = None,
    security_id: SecurityId | None = None,
) -> Decimal | None:
    """Compute the 20-session split-aware average dollar volume.

    Only bars falling on qualifying regular or half-day sessions are
    considered. When ``cutoff`` is supplied, only completed sessions strictly
    before its calendar date and bars known by ``known_at`` are visible. With
    no cutoff, the latest supplied qualifying session is used for backwards
    compatibility with this pure helper; the trusted assembler always passes
    explicit ``cutoff`` and ``known_at``.

    Split adjustments are applied to close and volume for bars whose
    ``trading_date`` precedes the split's ``ex_date``.  A split must be both
    effective by ``cutoff`` and visible by ``known_at``; its visibility is not
    incorrectly tied to the historical bar's retrieval time.
    """
    if type(bars) is not tuple or any(type(b) is not DailyBar for b in bars):
        raise ValueError("bars must be a tuple of DailyBar values")
    if len(bars) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("bars exceed the market snapshot item bound")
    for bar in bars:
        if type(
            bar._authority
        ) is not _BarProjectionAuthority or bar._authority.fingerprint != _daily_bar_fingerprint(
            bar
        ):
            raise ValueError("daily bar authority is not bound to frozen content")
    _validate_nyse_sessions(sessions)
    if type(split_adjustments) is not tuple or any(
        type(s) is not SplitAdjustment for s in split_adjustments
    ):
        raise ValueError("split_adjustments must be a tuple of SplitAdjustment values")
    for split in split_adjustments:
        split._verify_source_binding()
    if len(split_adjustments) > MAX_MARKET_SNAPSHOT_SPLITS:
        raise ValueError("split_adjustments exceed the market snapshot item bound")
    if any(not split.confirmed for split in split_adjustments):
        raise ValueError("split adjustments require an explicitly confirmed action")
    if split_adjustments and type(security_id) is not SecurityId:
        raise ValueError("split-aware ADV requires the target security id")
    if security_id is not None and type(security_id) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId or None")
    if security_id is not None and any(
        split.security_id != security_id for split in split_adjustments
    ):
        raise ValueError("split adjustments must bind to the target security")
    if security_id is None and bars:
        bar_security_ids = {bar.security_id for bar in bars}
        if len(bar_security_ids) != 1:
            raise ValueError("ADV bars must bind to one security")
        security_id = next(iter(bar_security_ids))
    if security_id is not None and any(bar.security_id != security_id for bar in bars):
        raise ValueError("ADV bars must bind to the target security")
    bar_identity_hashes = {
        bar._authority.identity_hash
        for bar in bars
        if type(bar._authority) is _BarProjectionAuthority
    }
    if split_adjustments and any(
        split.security_identity_hash not in bar_identity_hashes for split in split_adjustments
    ):
        raise ValueError("split adjustment does not bind to the historical-bar identity")
    split_dates = [split.ex_date for split in split_adjustments]
    if len(split_dates) != len(set(split_dates)):
        raise ValueError("split_adjustments must not repeat an ex-date")
    if cutoff is not None and type(cutoff) is not UtcTimestamp:
        raise ValueError("cutoff requires canonical UTC or None")
    if known_at is None and cutoff is not None:
        known_at = cutoff
    if known_at is not None and type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC or None")
    if cutoff is not None and known_at is not None and known_at.value > cutoff.value:
        raise ValueError("known_at cannot be after cutoff")

    session_dates = [session.trading_date for session in sessions]
    if len(session_dates) != len(set(session_dates)):
        raise ValueError("sessions must not contain duplicate trading dates")
    if session_dates != sorted(session_dates, key=lambda date: date.value):
        raise ValueError("sessions must be ordered by trading date")
    if any(bar.feed is not Feed.SIP_DELAYED for bar in bars):
        raise ValueError("ADV bars require the delayed SIP feed")

    bar_dates = [bar.trading_date for bar in bars]
    if bar_dates != sorted(bar_dates, key=lambda date: date.value):
        raise ValueError("bars must be ordered by trading date")
    session_by_date = {session.trading_date: session for session in sessions}
    if any(
        bar.trading_date not in session_by_date
        or session_by_date[bar.trading_date].day_kind is MarketDayKind.CLOSED
        or session_by_date[bar.trading_date].regular_session is None
        for bar in bars
    ):
        raise ValueError("every ADV bar must bind to an explicit open market session")

    cutoff_date = None if cutoff is None else cutoff.value.date()
    if cutoff_date is None and session_dates:
        cutoff_date = max(session_dates, key=lambda d: d.value).value

    qualifying_dates: set[TradingDate] = set()
    for session in sessions:
        if (
            session.day_kind is MarketDayKind.REGULAR or session.day_kind is MarketDayKind.HALF_DAY
        ) and (cutoff_date is None or session.trading_date.value < cutoff_date):
            qualifying_dates.add(session.trading_date)

    bar_by_date: dict[TradingDate, DailyBar] = {}
    for bar in bars:
        if bar.trading_date in bar_by_date:
            raise ValueError("bars must not contain duplicate trading dates")
        if cutoff is not None and bar.trading_date.value >= cutoff.value.date():
            raise ValueError("ADV bars dated at or after cutoff are not admissible")
        if known_at is not None and bar.available_at.value > known_at.value:
            raise ValueError("ADV bars known after known_at are not admissible")
        bar_by_date[bar.trading_date] = bar

    eligible: list[DailyBar] = []
    ordered_qualifying_dates = sorted(qualifying_dates, key=lambda d: d.value, reverse=True)
    if cutoff is not None:
        required_dates = ordered_qualifying_dates[:20]
        if len(required_dates) < 20 or any(date not in bar_by_date for date in required_dates):
            return None
        _require_complete_weekday_window(
            sessions,
            start=required_dates[-1],
            end_exclusive=cutoff.value.date(),
        )
        eligible = [bar_by_date[date] for date in required_dates]
    else:
        # The no-cutoff helper remains a compatibility utility for callers
        # that intentionally provide a sparse historical sample.  Snapshot
        # assembly always supplies an explicit cutoff and therefore uses the
        # strict latest-session authority above.
        for date in ordered_qualifying_dates:
            candidate_bar = bar_by_date.get(date)
            if candidate_bar is None:
                continue
            eligible.append(candidate_bar)
            if len(eligible) >= 20:
                break

        if len(eligible) >= 20 and cutoff_date is not None:
            _require_complete_weekday_window(
                sessions,
                start=eligible[-1].trading_date,
                end_exclusive=cutoff_date,
            )

    if len(eligible) < 20:
        return None

    total_dollar_volume = Decimal(0)
    for bar in eligible:
        close = bar.close
        volume = Decimal(bar.volume)
        for adj in split_adjustments:
            if cutoff is None:
                visible = (
                    adj.available_at.value <= bar.available_at.value
                    if known_at is None
                    else adj.available_at.value <= known_at.value
                )
                effective = True
            else:
                visible = known_at is not None and adj.available_at.value <= known_at.value
                effective = adj.ex_date.value <= cutoff.value.date()
            if visible and effective and bar.trading_date.value < adj.ex_date.value:
                close = close * Decimal(adj.denominator) / Decimal(adj.numerator)
                volume = volume * Decimal(adj.numerator) / Decimal(adj.denominator)
        total_dollar_volume += close * volume

    return total_dollar_volume / 20


def _quote_age(quote_observed_at: UtcTimestamp, as_of: UtcTimestamp) -> timedelta:
    """Return the exact wall-clock quote age without binary-float rounding."""
    return as_of.value - quote_observed_at.value


def _derive_freshness(
    *,
    quote: QuoteInput,
    as_of: UtcTimestamp,
    sessions: tuple[MarketSession, ...],
    max_age_seconds: int = QUOTE_MAX_AGE_SECONDS,
) -> tuple[Freshness, tuple[ClosedReason, ...]]:
    """Derive one quote's freshness from timestamps and the NYSE calendar.

    Future quotes, out-of-order quotes, and quotes on closed market days are
    ``CONFLICT`` and can never be tradable candidates.  A quote older than
    ``max_age_seconds`` is ``STALE``.
    """
    if quote.observed_at.value > as_of.value:
        return Freshness.CONFLICT, (ClosedReason.MARKET_DATA_CONFLICT,)
    if quote.received_at.value < quote.observed_at.value or quote.received_at.value > as_of.value:
        return Freshness.CONFLICT, (ClosedReason.MARKET_DATA_CONFLICT,)
    trading_date = TradingDate(as_of.value.date())
    session = next((s for s in sessions if s.trading_date == trading_date), None)
    if (
        session is None
        or session.day_kind is MarketDayKind.CLOSED
        or session.regular_session is None
        or not session.regular_session.contains(as_of)
        or not session.regular_session.contains(quote.observed_at)
    ):
        return Freshness.CONFLICT, (ClosedReason.MARKET_DATA_CONFLICT,)
    if type(max_age_seconds) is not int or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be a non-negative integer")
    age = _quote_age(quote.observed_at, as_of)
    if age > timedelta(seconds=max_age_seconds):
        return Freshness.STALE, (ClosedReason.QUOTE_MISSING_OR_STALE,)
    return Freshness.FRESH, ()


def validate_quote_age(
    *, as_of: UtcTimestamp, observed_at: UtcTimestamp, received_at: UtcTimestamp
) -> Freshness:
    """Validate quote temporal consistency without a calendar.

    Rejects future (observed_at > as_of) and out-of-order
    (received_at < observed_at) quotes; older quotes are STALE.
    """
    if type(as_of) is not UtcTimestamp or type(observed_at) is not UtcTimestamp:
        raise ValueError("as_of and observed_at require canonical UTC")
    if type(received_at) is not UtcTimestamp:
        raise ValueError("received_at requires canonical UTC")
    if observed_at.value > as_of.value:
        return Freshness.CONFLICT
    if received_at.value < observed_at.value or received_at.value > as_of.value:
        return Freshness.CONFLICT
    age = _quote_age(observed_at, as_of)
    if age > timedelta(seconds=QUOTE_MAX_AGE_SECONDS):
        return Freshness.STALE
    return Freshness.FRESH


def assemble_market_snapshot(
    *,
    security_id: SecurityId,
    symbol: SecuritySymbol,
    as_of: UtcTimestamp,
    known_at: UtcTimestamp,
    quote: QuoteInput,
    bars: tuple[DailyBar, ...],
    sessions: tuple[MarketSession, ...],
    split_adjustments: tuple[SplitAdjustment, ...] = (),
    last: Decimal | None = None,
    schema_version: SchemaVersion | None = None,
) -> MarketSnapshot:
    """Assemble one trusted market snapshot from raw quote/bar inputs.

    This is the only public build path.  It enforces the P4-C market rules:
    only Alpaca IEX latest quotes, exact feed/entitlement consistency,
    mandatory limited-coverage warning, 5-second quote age, 30 bps maximum
    spread, NYSE-calendar-consistent timestamps, and the 20-session
    split-aware ADV.  Findings that make the snapshot non-tradable are
    carried in ``reasons``; structurally invalid inputs are rejected.
    """
    if type(security_id) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    if type(symbol) is not SecuritySymbol:
        raise ValueError("symbol requires an exact SecuritySymbol")
    if type(as_of) is not UtcTimestamp:
        raise ValueError("as_of requires canonical UTC")
    if type(known_at) is not UtcTimestamp:
        raise ValueError("known_at requires canonical UTC")
    if known_at.value > as_of.value:
        raise ValueError("known_at cannot be after as_of")
    if type(quote) is not QuoteInput:
        raise ValueError("quote requires an exact QuoteInput")
    quote._verify_source_binding()
    if quote.symbol != symbol:
        raise ValueError("quote source symbol does not match the snapshot security")
    if type(bars) is not tuple or any(type(b) is not DailyBar for b in bars):
        raise ValueError("bars must be a tuple of DailyBar values")
    if len(bars) > MAX_MARKET_SNAPSHOT_ITEMS:
        raise ValueError("bars exceed the market snapshot item bound")
    for bar in bars:
        if type(
            bar._authority
        ) is not _BarProjectionAuthority or bar._authority.fingerprint != _daily_bar_fingerprint(
            bar
        ):
            raise ValueError("daily bar authority is not bound to frozen content")
    if any(bar.security_id != security_id for bar in bars):
        raise ValueError("market bars must bind to the snapshot security")
    _validate_nyse_sessions(sessions)
    if type(split_adjustments) is not tuple or any(
        type(s) is not SplitAdjustment for s in split_adjustments
    ):
        raise ValueError("split_adjustments must be a tuple of SplitAdjustment values")
    for split in split_adjustments:
        split._verify_source_binding()
    if len(split_adjustments) > MAX_MARKET_SNAPSHOT_SPLITS:
        raise ValueError("split_adjustments exceed the market snapshot item bound")
    if any(split.security_id != security_id for split in split_adjustments):
        raise ValueError("split adjustments must bind to the snapshot security")
    if last is not None and (type(last) is not Decimal or not last.is_finite() or last <= 0):
        raise ValueError("last must be a positive finite Decimal or None")
    if schema_version is None:
        schema_version = SchemaVersion("1.0.0")
    if type(schema_version) is not SchemaVersion:
        raise ValueError("schema_version requires an exact SchemaVersion")

    if quote.feed is not Feed.IEX:
        raise ValueError("latest quotes accept only the Alpaca IEX feed")
    if quote.entitlement is not Entitlement.IEX:
        raise ValueError("IEX quotes require the IEX entitlement")
    if quote.observed_at.value > known_at.value or quote.received_at.value > known_at.value:
        raise ValueError("quote is not visible at known_at")

    mid = _mid_exact(quote.bid, quote.ask)
    if last is not None and last != mid:
        raise ValueError("last must be the source-derived quote mid")
    # The latest quote's midpoint is the only admissible last-price authority
    # in this snapshot.  A caller cannot inject an independent scalar.
    last = mid
    _, spread_too_wide = _spread_exact(quote.bid, quote.ask)
    reasons: set[ClosedReason] = set()

    freshness, freshness_reasons = _derive_freshness(quote=quote, as_of=as_of, sessions=sessions)
    if freshness is Freshness.CONFLICT:
        raise ValueError("quote timestamp is not valid for the open NYSE session")
    reasons.update(freshness_reasons)

    if spread_too_wide:
        reasons.add(ClosedReason.SPREAD_TOO_WIDE)

    adv20_usd = compute_adv20(
        bars,
        sessions,
        split_adjustments,
        cutoff=as_of,
        known_at=known_at,
        security_id=security_id,
    )
    if adv20_usd is None:
        reasons.add(ClosedReason.ADV_BELOW_MINIMUM)

    bar_refs = tuple(sorted({bar.source_ref for bar in bars}, key=lambda r: r.record_id))
    bar_dates = tuple(sorted({bar.trading_date for bar in bars}, key=lambda date: date.value))
    split_adjustment_refs = tuple(
        sorted(
            {split.source_ref for split in split_adjustments},
            key=lambda ref: (ref.record_id, ref.family.value, ref.record_hash),
        )
    )
    bar_feed = None
    if bars:
        feeds = {bar.feed for bar in bars}
        if len(feeds) != 1:
            reasons.add(ClosedReason.MARKET_DATA_CONFLICT)
        bar_feed = next(iter(feeds))

    return _finalize_market_snapshot(
        security_id=security_id,
        symbol=symbol,
        as_of=as_of,
        known_at=known_at,
        received_at=quote.received_at,
        observed_at=quote.observed_at,
        feed=quote.feed,
        entitlement=quote.entitlement,
        bid=quote.bid,
        ask=quote.ask,
        quote_source_ref=quote.source_ref,
        coverage=Coverage.LIMITED_MARKET_COVERAGE,
        freshness=freshness,
        producer_version=_PRODUCER_VERSION,
        schema_version=schema_version,
        last=last,
        adv20_usd=adv20_usd,
        bar_feed=bar_feed,
        bar_refs=bar_refs,
        bar_dates=bar_dates,
        sessions=sessions,
        split_adjustment_refs=split_adjustment_refs,
        split_adjustments=split_adjustments,
        coverage_warning="IEX limited market coverage",
        reasons=_canonical_reasons(tuple(reasons)),
    )


def _finalize_market_snapshot(**values: object) -> MarketSnapshot:
    """Finalize values that came from the trusted assembler or verified readback."""
    bid = values.get("bid")
    ask = values.get("ask")
    if type(bid) is not Decimal or type(ask) is not Decimal:
        raise ValueError("bid and ask must be exact Decimal values")
    if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask <= 0:
        raise ValueError("bid and ask must be positive finite Decimals")
    if bid > ask:
        raise ValueError("bid must not exceed ask")
    mid = _mid_exact(bid, ask)
    spread_bps, _ = _spread_exact(bid, ask)
    if "mid" in values and values["mid"] != mid:
        raise ValueError("mid must be derived from bid and ask")
    if "spread_bps" in values and values["spread_bps"] != spread_bps:
        raise ValueError("spread_bps must be derived from bid and ask")
    body: dict[str, object] = {
        "mid": mid,
        "spread_bps": spread_bps,
    }
    for key, value in values.items():
        if key not in ("mid", "spread_bps", "snapshot_hash"):
            body[key] = value
    for dataclass_field in fields(MarketSnapshot):
        if dataclass_field.name not in body and dataclass_field.name not in (
            "snapshot_hash",
            "_authority",
        ):
            body[dataclass_field.name] = _field_default(dataclass_field)
    body["_authority"] = _MARKET_SNAPSHOT_AUTHORITY
    _validate_market_snapshot_fields(body)
    provisional = object.__new__(MarketSnapshot)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "snapshot_hash", "")
    computed = provisional.compute_hash()
    body["snapshot_hash"] = computed
    body["mid"] = mid
    body["spread_bps"] = spread_bps
    return MarketSnapshot(**body)  # type: ignore[arg-type]


def _reconstruct_market_snapshot(*, authority: object, **values: object) -> MarketSnapshot:
    """Reconstruct one snapshot after a trusted persistence readback.

    Persistence must validate source-record lineage and all derived values
    before calling this capability.  Keeping the capability token private
    prevents arbitrary application callers from turning caller-supplied
    derived fields into a new market authority merely by hashing them.
    """
    if authority is not _MARKET_SNAPSHOT_READBACK_AUTHORITY:
        raise ValueError("market snapshot reconstruction requires trusted readback authority")
    snapshot_hash = values.get("snapshot_hash")
    if type(snapshot_hash) is not str or _HASH_TEXT.fullmatch(snapshot_hash) is None:
        raise ValueError("snapshot_hash must be a SHA-256 digest")
    snapshot = _finalize_market_snapshot(**values)
    if snapshot.snapshot_hash != snapshot_hash:
        raise ValueError("snapshot hash does not match frozen content")
    return snapshot


def reconstruct_market_snapshot(**values: object) -> MarketSnapshot:
    """Reject arbitrary public reconstruction of a market authority."""
    del values
    raise ValueError("market snapshot reconstruction is a trusted readback-only API")


def build_market_snapshot(**values: object) -> MarketSnapshot:
    """Reject the former untrusted public constructor."""
    del values
    raise ValueError("build_market_snapshot is not an ingestion API; use assemble_market_snapshot")
