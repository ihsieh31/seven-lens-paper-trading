"""PostgreSQL adapter for P4-C market snapshots.

Every write goes through the migration's narrow SECURITY DEFINER function
``append_market_snapshot``; this adapter never writes the append-only table
directly and never updates or deletes anything.  Every read rebuilds the
exact domain record from the stored wire form, and the record constructor's
hash verification is the readback re-verification: a tampered or drifted row
fails closed.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Final

import psycopg
from psycopg.types.json import Jsonb

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.clock.market_clock import MarketDayKind, MarketSession, RegularSessionWindow
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.market_data.snapshots import (
    _MARKET_SNAPSHOT_READBACK_AUTHORITY,
    MAX_MARKET_SNAPSHOT_BYTES,
    Coverage,
    Entitlement,
    Feed,
    Freshness,
    MarketSnapshot,
    SplitAdjustment,
    _reconstruct_market_snapshot,
    _reconstruct_split_adjustment,
)
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import SecurityId, SecuritySymbol, SourceRef
from seven_lens.securities.corporate_actions import CorporateActionType
from seven_lens.sources.roles import P4SourceFamily

_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REF_KEYS: Final = {"record_id", "family", "record_hash"}
_SNAPSHOT_KEYS: Final = {
    "security_id",
    "symbol",
    "as_of",
    "known_at",
    "observed_at",
    "received_at",
    "feed",
    "entitlement",
    "bid",
    "ask",
    "mid",
    "spread_bps",
    "last",
    "adv20_usd",
    "bar_feed",
    "bar_refs",
    "bar_dates",
    "sessions",
    "split_adjustment_refs",
    "split_adjustments",
    "quote_source_ref",
    "coverage",
    "freshness",
    "coverage_warning",
    "reasons",
    "producer_version",
    "schema_version",
}


class PostgresMarketDataError(RuntimeError):
    """Raised when PostgreSQL rejects a market-snapshot write or read."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class PostgresMarketSnapshotStore:
    """Append-only market snapshot authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, snapshot: MarketSnapshot) -> AppendOutcome:
        """Append one snapshot through the DB hash authority."""
        if type(snapshot) is not MarketSnapshot:
            raise ValueError("only an exact MarketSnapshot can be appended")
        snapshot.verify_integrity()
        wire = snapshot.wire()
        _assert_wire_size(wire, "market snapshot")
        try:
            row = self._connection.execute(
                "SELECT public.append_market_snapshot(%s, %s)",
                (snapshot.snapshot_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("market snapshot append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresMarketDataError("market snapshot authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, snapshot_hash: str) -> MarketSnapshot | None:
        """Return one exact snapshot by hash, or None."""
        if type(snapshot_hash) is not str or _HASH_TEXT.fullmatch(snapshot_hash) is None:
            raise ValueError("snapshot_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT snapshot_hash, wire
            FROM public.market_snapshots
            WHERE snapshot_hash = %s
            """,
            (snapshot_hash,),
        ).fetchone()
        if row is None:
            return None
        return _snapshot_from_wire(_wire(row[1], "market snapshot"), str(row[0]))

    def latest_for_security(self, security_id: SecurityId) -> MarketSnapshot | None:
        """Return the most recent snapshot for one security, or None."""
        if type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        row = self._connection.execute(
            """
            SELECT snapshot_hash, wire
            FROM public.market_snapshots
            WHERE security_id = %s
            ORDER BY appended_at DESC, snapshot_hash DESC
            LIMIT 1
            """,
            (security_id.value,),
        ).fetchone()
        if row is None:
            return None
        return _snapshot_from_wire(_wire(row[1], "market snapshot"), str(row[0]))

    def snapshots(self) -> tuple[MarketSnapshot, ...]:
        """Return every stored snapshot."""
        rows = self._connection.execute(
            """
            SELECT snapshot_hash, wire
            FROM public.market_snapshots
            ORDER BY appended_at, snapshot_hash
            """
        ).fetchall()
        return tuple(
            _snapshot_from_wire(_wire(row[1], "market snapshot"), str(row[0])) for row in rows
        )

    def count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM public.market_snapshots").fetchone()
        if row is None or type(row[0]) is not int:
            raise PostgresMarketDataError("market snapshot count returned an invalid result")
        return row[0]


def _wire(value: object, operation: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PostgresMarketDataError(f"{operation} wire form must be a JSON object")
    _assert_wire_size(value, operation)
    return value


def _assert_wire_size(wire: dict[str, object], operation: str) -> None:
    """Apply the same canonical UTF-8 resource bound as the DB authority."""
    try:
        size = len(
            json.dumps(
                wire,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise PostgresMarketDataError(f"{operation} wire is not canonical JSON") from error
    if size > MAX_MARKET_SNAPSHOT_BYTES:
        raise PostgresMarketDataError(
            f"{operation} wire exceeds the {MAX_MARKET_SNAPSHOT_BYTES}-byte limit"
        )


def _translate(message: str, error: psycopg.Error) -> PostgresMarketDataError:
    sqlstate = error.sqlstate
    if sqlstate == "40001":
        message = f"{message}: concurrent transition lost"
    elif sqlstate == "23514":
        message = f"{message}: storage constraint violated"
    return PostgresMarketDataError(message, sqlstate=sqlstate)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise PostgresMarketDataError(f"wire {field_name} must be text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is not None and type(value) is not str:
        raise PostgresMarketDataError(f"wire {field_name} must be text or null")
    return value


def _timestamp_text(value: object, field_name: str) -> UtcTimestamp:
    return UtcTimestamp.from_isoformat(_text(value, field_name))


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not str:
        raise PostgresMarketDataError(f"wire {field_name} must be a decimal string")
    try:
        return Decimal(value)
    except Exception as error:
        raise PostgresMarketDataError(f"wire {field_name} is not a valid decimal") from error


def _int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise PostgresMarketDataError(f"wire {field_name} must be an integer")
    return value


def _source_refs(value: object, *, max_items: int | None = None) -> tuple[SourceRef, ...]:
    if type(value) is not list:
        raise PostgresMarketDataError("wire source_refs must be an array")
    if max_items is not None and len(value) > max_items:
        raise PostgresMarketDataError("wire source_refs exceeds its item bound")
    refs: list[SourceRef] = []
    for item in value:
        if type(item) is not dict or set(item) != _SOURCE_REF_KEYS:
            raise PostgresMarketDataError("wire source_refs entries must have an exact shape")
        refs.append(
            SourceRef(
                record_id=_text(item.get("record_id"), "source_refs.record_id"),
                family=P4SourceFamily(_text(item.get("family"), "source_refs.family")),
                record_hash=_text(item.get("record_hash"), "source_refs.record_hash"),
            )
        )
    return tuple(refs)


def _quote_source_ref(value: object) -> SourceRef:
    if type(value) is not dict or set(value) != _SOURCE_REF_KEYS:
        raise PostgresMarketDataError("wire quote_source_ref must have an exact shape")
    return SourceRef(
        record_id=_text(value.get("record_id"), "quote_source_ref.record_id"),
        family=P4SourceFamily(_text(value.get("family"), "quote_source_ref.family")),
        record_hash=_text(value.get("record_hash"), "quote_source_ref.record_hash"),
    )


def _sessions(value: object) -> tuple[MarketSession, ...]:
    if type(value) is not list:
        raise PostgresMarketDataError("wire sessions must be an array")
    if len(value) > 1024:
        raise PostgresMarketDataError("wire sessions exceeds its item bound")
    sessions: list[MarketSession] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "trading_date",
            "day_kind",
            "opens_at",
            "closes_at",
        }:
            raise PostgresMarketDataError("wire sessions entries must have an exact shape")
        day_kind = MarketDayKind(_text(item.get("day_kind"), "sessions.day_kind"))
        opens_at = item.get("opens_at")
        closes_at = item.get("closes_at")
        if day_kind is MarketDayKind.CLOSED:
            if opens_at is not None or closes_at is not None:
                raise PostgresMarketDataError("closed sessions cannot carry open/close times")
            regular_session = None
        else:
            if type(opens_at) is not str or type(closes_at) is not str:
                raise PostgresMarketDataError("open sessions require open/close timestamps")
            regular_session = RegularSessionWindow(
                opens_at=UtcTimestamp.from_isoformat(opens_at),
                closes_at=UtcTimestamp.from_isoformat(closes_at),
            )
        sessions.append(
            MarketSession(
                trading_date=TradingDate.from_isoformat(
                    _text(item.get("trading_date"), "sessions.trading_date")
                ),
                day_kind=day_kind,
                regular_session=regular_session,
            )
        )
    return tuple(sessions)


def _split_adjustments(value: object) -> tuple[SplitAdjustment, ...]:
    if type(value) is not list:
        raise PostgresMarketDataError("wire split_adjustments must be an array")
    if len(value) > 64:
        raise PostgresMarketDataError("wire split_adjustments exceeds its item bound")
    adjustments: list[SplitAdjustment] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "security_id",
            "ex_date",
            "numerator",
            "denominator",
            "event_id",
            "event_record_hash",
            "security_identity_hash",
            "action_type",
            "effective_date",
            "source_ref",
            "source_refs",
            "available_at",
            "confirmed",
        }:
            raise PostgresMarketDataError("wire split_adjustments entries must have an exact shape")
        confirmed = item.get("confirmed")
        if type(confirmed) is not bool:
            raise PostgresMarketDataError("wire split_adjustments.confirmed must be boolean")
        try:
            adjustments.append(
                _reconstruct_split_adjustment(
                    authority=_MARKET_SNAPSHOT_READBACK_AUTHORITY,
                    event_id=_text(item.get("event_id"), "split.event_id"),
                    event_record_hash=_text(
                        item.get("event_record_hash"), "split.event_record_hash"
                    ),
                    security_identity_hash=_text(
                        item.get("security_identity_hash"),
                        "split.security_identity_hash",
                    ),
                    action_type=CorporateActionType(
                        _text(item.get("action_type"), "split.action_type")
                    ),
                    security_id=SecurityId(_text(item.get("security_id"), "split.security_id")),
                    ex_date=TradingDate.from_isoformat(_text(item.get("ex_date"), "split.ex_date")),
                    effective_date=TradingDate.from_isoformat(
                        _text(item.get("effective_date"), "split.effective_date")
                    ),
                    numerator=_int(item.get("numerator"), "split.numerator"),
                    denominator=_int(item.get("denominator"), "split.denominator"),
                    source_ref=_quote_source_ref(item.get("source_ref")),
                    source_refs=_source_refs(item.get("source_refs"), max_items=64),
                    available_at=_timestamp_text(item.get("available_at"), "split.available_at"),
                    confirmed=confirmed,
                )
            )
        except ValueError as error:
            raise PostgresMarketDataError(
                "wire split_adjustments contains an invalid value"
            ) from error
    return tuple(adjustments)


def _snapshot_from_wire(wire: dict[str, object], snapshot_hash: str) -> MarketSnapshot:
    """Rebuild one market snapshot from its stored wire; the constructor re-verifies."""
    if set(wire) != _SNAPSHOT_KEYS:
        raise PostgresMarketDataError("market snapshot wire has an unexpected shape")
    reasons = wire.get("reasons")
    if type(reasons) is not list:
        raise PostgresMarketDataError("market snapshot wire reasons must be an array")
    reasons_tuple = tuple(ClosedReason(_text(item, "reasons.item")) for item in reasons)
    bar_refs = wire.get("bar_refs")
    if type(bar_refs) is not list:
        raise PostgresMarketDataError("market snapshot wire bar_refs must be an array")
    bar_refs_tuple = _source_refs(bar_refs, max_items=1024)
    bar_dates = wire.get("bar_dates")
    if type(bar_dates) is not list:
        raise PostgresMarketDataError("market snapshot wire bar_dates must be an array")
    if len(bar_dates) > 1024:
        raise PostgresMarketDataError("market snapshot wire bar_dates exceeds its item bound")
    try:
        bar_dates_tuple = tuple(
            TradingDate.from_isoformat(_text(item, "bar_dates.item")) for item in bar_dates
        )
    except ValueError as error:
        raise PostgresMarketDataError("market snapshot wire bar_dates are invalid") from error
    try:
        sessions_tuple = _sessions(wire.get("sessions"))
    except ValueError as error:
        raise PostgresMarketDataError("market snapshot wire sessions are invalid") from error
    split_refs = wire.get("split_adjustment_refs")
    if type(split_refs) is not list:
        raise PostgresMarketDataError("market snapshot wire split_adjustment_refs must be an array")
    split_refs_tuple = _source_refs(split_refs, max_items=64)
    try:
        split_adjustments_tuple = _split_adjustments(wire.get("split_adjustments"))
    except ValueError as error:
        raise PostgresMarketDataError(
            "market snapshot wire split_adjustments are invalid"
        ) from error
    try:
        return _reconstruct_market_snapshot(
            authority=_MARKET_SNAPSHOT_READBACK_AUTHORITY,
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            symbol=SecuritySymbol(_text(wire.get("symbol"), "symbol")),
            as_of=_timestamp_text(wire.get("as_of"), "as_of"),
            known_at=_timestamp_text(wire.get("known_at"), "known_at"),
            observed_at=_timestamp_text(wire.get("observed_at"), "observed_at"),
            received_at=_timestamp_text(wire.get("received_at"), "received_at"),
            feed=Feed(_text(wire.get("feed"), "feed")),
            entitlement=Entitlement(_text(wire.get("entitlement"), "entitlement")),
            bid=_decimal(wire.get("bid"), "bid"),
            ask=_decimal(wire.get("ask"), "ask"),
            mid=_decimal(wire.get("mid"), "mid"),
            spread_bps=_int(wire.get("spread_bps"), "spread_bps"),
            last=None if wire.get("last") is None else _decimal(wire.get("last"), "last"),
            adv20_usd=(
                None
                if wire.get("adv20_usd") is None
                else _decimal(wire.get("adv20_usd"), "adv20_usd")
            ),
            bar_feed=(
                None
                if wire.get("bar_feed") is None
                else Feed(_text(wire.get("bar_feed"), "bar_feed"))
            ),
            bar_refs=bar_refs_tuple,
            bar_dates=bar_dates_tuple,
            sessions=sessions_tuple,
            split_adjustment_refs=split_refs_tuple,
            split_adjustments=split_adjustments_tuple,
            quote_source_ref=_quote_source_ref(wire.get("quote_source_ref")),
            coverage=Coverage(_text(wire.get("coverage"), "coverage")),
            freshness=Freshness(_text(wire.get("freshness"), "freshness")),
            coverage_warning=_optional_text(wire.get("coverage_warning"), "coverage_warning"),
            reasons=reasons_tuple,
            producer_version=_text(wire.get("producer_version"), "producer_version"),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            snapshot_hash=snapshot_hash,
        )
    except ValueError as error:
        raise PostgresMarketDataError("stored market snapshot failed reconstruction") from error
