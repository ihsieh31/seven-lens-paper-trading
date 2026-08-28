"""PostgreSQL adapter for the P4-B security master and source record log.

Every write goes through one of the migration's narrow SECURITY DEFINER
functions; this adapter never writes the append-only tables directly and
never unblocks, rewrites, or deletes anything.  Every read rebuilds exact
domain records from the stored wire form, and the record constructors'
hash verification is the readback re-verification: a tampered or drifted
row fails closed instead of reading back as safe.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

import psycopg
from psycopg.types.json import Jsonb

from seven_lens.application.ports.p4_source_records import (
    AppendOutcome,
    P4SourceRecordLog,
    RecordLineageError,
)
from seven_lens.application.ports.securities import (
    CorporateActionEventStore,
    QuarantineDecisionStore,
    SecurityIdentityStore,
)
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.securities.contracts import (
    AssetClass,
    Cik,
    Cusip,
    Isin,
    ListingExchange,
    SecurityId,
    SecurityIdentityRecord,
    SecurityStatus,
    SecuritySymbol,
    SourceRef,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    CorporateActionState,
    CorporateActionType,
    SplitRatio,
    validate_lineage,
)
from seven_lens.securities.quarantine import (
    QuarantineDecision,
    QuarantineOutcome,
    QuarantineReason,
)
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    build_normalized_record,
    canonical_payload,
)
from seven_lens.sources.roles import P4SourceFamily

_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_SUPERSESSION_MESSAGE: Final = "explicit supersession"


class PostgresSecuritiesError(RuntimeError):
    """Raised when PostgreSQL rejects security-master identity, lineage, or CAS."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class PostgresP4RecordLog:
    """Durable append-only P4-A source record log with supersession chains."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, record: NormalizedSourceRecord) -> AppendOutcome:
        """Append one validated record through the DB lineage authority."""
        if type(record) is not NormalizedSourceRecord:
            raise ValueError("only an exact NormalizedSourceRecord can be appended")
        record.verify_integrity()
        try:
            row = self._connection.execute(
                "SELECT public.append_p4_source_record(%s, %s, %s, %s)",
                (
                    record.record_id,
                    record.record_hash,
                    record.content_hash,
                    Jsonb(record.wire()),
                ),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate_record_append(error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresSecuritiesError("source record authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, record_id: str) -> NormalizedSourceRecord | None:
        """Return the current head version for one identifier, or None."""
        if type(record_id) is not str or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError("record_id must be a canonical record identifier")
        row = self._connection.execute(
            """
            SELECT record_hash, wire
            FROM public.p4_source_records
            WHERE record_id = %s
            ORDER BY append_sequence DESC
            LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return _source_record_from_wire(_wire(row[1], "source record"), str(row[0]))

    def get_version(self, record_id: str, record_hash: str) -> NormalizedSourceRecord | None:
        """Return one exact immutable source version, never the current head."""
        if type(record_id) is not str or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError("record_id must be a canonical record identifier")
        if type(record_hash) is not str or _HASH_TEXT.fullmatch(record_hash) is None:
            raise ValueError("record_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT record_hash, wire
            FROM public.p4_source_records
            WHERE record_id = %s AND record_hash = %s
            """,
            (record_id, record_hash),
        ).fetchone()
        if row is None:
            return None
        return _source_record_from_wire(_wire(row[1], "source record"), str(row[0]))

    def versions(self, record_id: str) -> tuple[NormalizedSourceRecord, ...]:
        """Return every immutable source version in append order."""
        if type(record_id) is not str or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError("record_id must be a canonical record identifier")
        rows = self._connection.execute(
            """
            SELECT record_hash, wire
            FROM public.p4_source_records
            WHERE record_id = %s
            ORDER BY append_sequence
            """,
            (record_id,),
        ).fetchall()
        return tuple(
            _source_record_from_wire(_wire(row[1], "source record"), str(row[0])) for row in rows
        )

    def lock_record(self, record_id: str) -> None:
        """Serialize source correction against a consuming authority transaction."""
        if type(record_id) is not str or _RECORD_ID.fullmatch(record_id) is None:
            raise ValueError("record_id must be a canonical record identifier")
        self._connection.execute(
            "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext(%s))",
            (f"p4b.source-record:{record_id}",),
        )

    def records(self) -> tuple[NormalizedSourceRecord, ...]:
        """Return current versions ordered by first appearance."""
        rows = self._connection.execute(
            """
            SELECT r.record_hash, r.wire
            FROM public.p4_source_records AS r
            JOIN (
                SELECT record_id,
                       min(append_sequence) AS first_sequence,
                       max(append_sequence) AS head_sequence
                FROM public.p4_source_records
                GROUP BY record_id
            ) AS generations
              ON generations.record_id = r.record_id
             AND generations.head_sequence = r.append_sequence
            ORDER BY generations.first_sequence
            """
        ).fetchall()
        return tuple(
            _source_record_from_wire(_wire(row[1], "source record"), str(row[0])) for row in rows
        )

    def count(self) -> int:
        row = self._connection.execute(
            "SELECT count(DISTINCT record_id) FROM public.p4_source_records"
        ).fetchone()
        if row is None or type(row[0]) is not int:
            raise PostgresSecuritiesError("source record count returned an invalid result")
        return row[0]


class PostgresSecurityMaster:
    """Identity, split event, and quarantine-decision authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run one security-master unit in a single connection transaction."""
        with self._connection.transaction():
            yield

    def append_identity(self, record: SecurityIdentityRecord) -> AppendOutcome:
        """Append one identity observation through the DB head authority."""
        if type(record) is not SecurityIdentityRecord:
            raise ValueError("only an exact SecurityIdentityRecord can be appended")
        record.verify_integrity()
        try:
            row = self._connection.execute(
                "SELECT public.append_security_identity(%s, %s)",
                (record.identity_hash, Jsonb(record.wire())),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("identity append failed", error) from error
        return _outcome(row, "identity authority")

    def identity_records(
        self,
        *,
        security_id: SecurityId | None = None,
        symbol: SecuritySymbol | None = None,
    ) -> tuple[SecurityIdentityRecord, ...]:
        """Return stored observations scoped exactly like the resolver."""
        if security_id is not None and type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if symbol is not None and type(symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if security_id is None and symbol is None:
            raise ValueError("identity query requires security_id or symbol")
        if security_id is not None:
            rows = self._connection.execute(
                """
                SELECT identity_hash, wire
                FROM public.security_identities
                WHERE security_id = %s
                ORDER BY appended_at, identity_hash
                """,
                (security_id.value,),
            ).fetchall()
        else:
            assert symbol is not None
            rows = self._connection.execute(
                """
                SELECT identity_hash, wire
                FROM public.security_identities
                WHERE symbol = %s
                ORDER BY appended_at, identity_hash
                """,
                (symbol.value,),
            ).fetchall()
        return tuple(
            _identity_from_wire(_wire(row[1], "identity record"), str(row[0])) for row in rows
        )

    def append_event(
        self, record: CorporateActionRecord, *, previous_record_hash: str | None
    ) -> AppendOutcome:
        """Append one lineage row under the closed transition table and CAS."""
        if type(record) is not CorporateActionRecord:
            raise ValueError("only an exact CorporateActionRecord can be appended")
        record.verify_integrity()
        if previous_record_hash is not None and (
            type(previous_record_hash) is not str
            or _HASH_TEXT.fullmatch(previous_record_hash) is None
        ):
            raise ValueError("previous_record_hash must be a SHA-256 digest or None")
        try:
            row = self._connection.execute(
                "SELECT public.append_corporate_action_event(%s, %s, %s)",
                (record.record_hash, previous_record_hash, Jsonb(record.wire())),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("corporate-action append failed", error) from error
        return _outcome(row, "corporate-action authority")

    def event_lineage(self, event_id: str) -> tuple[CorporateActionRecord, ...]:
        """Return one lineage from its DETECTED root to its head, or empty."""
        if type(event_id) is not str or _RECORD_ID.fullmatch(event_id) is None:
            raise ValueError("event_id must be a canonical event identifier")
        rows = self._connection.execute(
            """
            SELECT record_hash, previous_record_hash, wire
            FROM public.corporate_action_events
            WHERE event_id = %s
            """,
            (event_id,),
        ).fetchall()
        if not rows:
            return ()
        by_hash: dict[str, tuple[str | None, dict[str, object]]] = {}
        successors: dict[str | None, list[str]] = {}
        for row in rows:
            record_hash = str(row[0])
            previous = None if row[1] is None else str(row[1])
            if record_hash in by_hash:
                raise PostgresSecuritiesError("event lineage carries duplicate record hashes")
            by_hash[record_hash] = (previous, _wire(row[2], "corporate-action record"))
            successors.setdefault(previous, []).append(record_hash)
        roots = successors.get(None, [])
        if len(roots) != 1:
            raise PostgresSecuritiesError("event lineage must carry exactly one root")
        chain: list[CorporateActionRecord] = []
        current: str | None = roots[0]
        seen: set[str] = set()
        while current is not None:
            if current in seen:
                raise PostgresSecuritiesError("event lineage carries a hash cycle")
            seen.add(current)
            _, wire = by_hash[current]
            chain.append(_event_from_wire(wire, current))
            followers = successors.get(current, [])
            if len(followers) > 1:
                raise PostgresSecuritiesError("event lineage carries a fork")
            current = followers[0] if followers else None
        if len(seen) != len(by_hash):
            raise PostgresSecuritiesError("event lineage carries unreachable rows")
        lineage = tuple(chain)
        validate_lineage(lineage)
        return lineage

    def security_event_ids(self, security_id: SecurityId) -> tuple[str, ...]:
        """Return every event id ever observed for one security."""
        if type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        rows = self._connection.execute(
            """
            SELECT DISTINCT event_id
            FROM public.corporate_action_events
            WHERE security_id = %s
            ORDER BY event_id
            """,
            (security_id.value,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def record_decision(self, decision: QuarantineDecision) -> AppendOutcome:
        """Record one content-addressed decision; identical hashes are idempotent."""
        if type(decision) is not QuarantineDecision:
            raise ValueError("only an exact QuarantineDecision can be recorded")
        decision.verify_integrity()
        try:
            row = self._connection.execute(
                "SELECT public.record_quarantine_decision(%s, %s)",
                (decision.decision_hash, Jsonb(decision.wire())),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("quarantine decision record failed", error) from error
        return _outcome(row, "quarantine decision authority")

    def latest_decision(self, security_id: SecurityId) -> QuarantineDecision | None:
        """Return the most recent decision for one security, or None."""
        if type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        row = self._connection.execute(
            """
            SELECT decision_hash, wire
            FROM public.security_quarantine_decisions
            WHERE security_id = %s
            ORDER BY decision_at DESC, decision_hash DESC
            LIMIT 1
            """,
            (security_id.value,),
        ).fetchone()
        if row is None:
            return None
        return _decision_from_wire(_wire(row[1], "quarantine decision"), str(row[0]))


def _outcome(row: Any, authority: str) -> AppendOutcome:
    if row is None or type(row[0]) is not str:
        raise PostgresSecuritiesError(f"{authority} returned an invalid result")
    return AppendOutcome(row[0])


def _wire(value: object, operation: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PostgresSecuritiesError(f"{operation} wire form must be a JSON object")
    return value


def _translate(message: str, error: psycopg.Error) -> PostgresSecuritiesError:
    sqlstate = error.sqlstate
    if sqlstate == "40001":
        message = f"{message}: concurrent transition lost"
    elif sqlstate == "23514":
        message = f"{message}: lineage constraint violated"
    return PostgresSecuritiesError(message, sqlstate=sqlstate)


def _translate_record_append(error: psycopg.Error) -> PostgresSecuritiesError | RecordLineageError:
    sqlstate = error.sqlstate
    if sqlstate == "23514" and _SUPERSESSION_MESSAGE in str(error):
        return RecordLineageError(
            "same provider identity with different content requires explicit supersession"
        )
    return _translate("source record append failed", error)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise PostgresSecuritiesError(f"wire {field_name} must be text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is not None and type(value) is not str:
        raise PostgresSecuritiesError(f"wire {field_name} must be text or null")
    return value


def _timestamp_text(value: object, field_name: str) -> UtcTimestamp:
    return UtcTimestamp.from_isoformat(_text(value, field_name))


def _source_refs(value: object) -> tuple[SourceRef, ...]:
    if type(value) is not list:
        raise PostgresSecuritiesError("wire source_refs must be an array")
    refs: list[SourceRef] = []
    for item in value:
        if type(item) is not dict:
            raise PostgresSecuritiesError("wire source_refs entries must be objects")
        refs.append(
            SourceRef(
                record_id=_text(item.get("record_id"), "source_refs.record_id"),
                family=P4SourceFamily(_text(item.get("family"), "source_refs.family")),
                record_hash=_text(item.get("record_hash"), "source_refs.record_hash"),
            )
        )
    return tuple(refs)


def _source_record_from_wire(wire: dict[str, object], record_hash: str) -> NormalizedSourceRecord:
    """Rebuild one P4-A record from its stored wire and re-verify its hash."""
    values: dict[str, object] = {
        "record_id": _text(wire.get("record_id"), "record_id"),
        "family": P4SourceFamily(_text(wire.get("family"), "family")),
        "endpoint_id": _text(wire.get("endpoint_id"), "endpoint_id"),
        "schema_version": SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
        "content_hash": _text(wire.get("content_hash"), "content_hash"),
        "retrieved_at": _timestamp_text(wire.get("retrieved_at"), "retrieved_at"),
        "payload": canonical_payload(wire.get("payload")),
        "material_claim": wire.get("material_claim"),
    }
    for name in ("observation_at", "published_at", "available_at", "effective_at"):
        raw = wire.get(name)
        values[name] = None if raw is None else _timestamp_text(raw, name)
    vintage = wire.get("vintage")
    if vintage is not None:
        if type(vintage) is not list or len(vintage) != 2:
            raise PostgresSecuritiesError("wire vintage must be a start/end pair")
        values["vintage"] = (_text(vintage[0], "vintage.start"), _text(vintage[1], "vintage.end"))
    supersedes = wire.get("supersedes_content_hash")
    values["supersedes_content_hash"] = _optional_text(supersedes, "supersedes_content_hash")
    warning = wire.get("coverage_warning")
    values["coverage_warning"] = _optional_text(warning, "coverage_warning")
    try:
        record = build_normalized_record(**values)
    except ValueError as error:
        raise PostgresSecuritiesError("stored source record failed reconstruction") from error
    if record.record_hash != record_hash:
        raise PostgresSecuritiesError("stored source record hash does not match its lineage row")
    return record


def _identity_from_wire(wire: dict[str, object], identity_hash: str) -> SecurityIdentityRecord:
    """Rebuild one identity record from its stored wire; the constructor re-verifies."""
    valid_to = wire.get("valid_to")
    try:
        return SecurityIdentityRecord(
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            symbol=SecuritySymbol(_text(wire.get("symbol"), "symbol")),
            exchange=ListingExchange(_text(wire.get("exchange"), "exchange")),
            asset_class=AssetClass(_text(wire.get("asset_class"), "asset_class")),
            valid_from=_timestamp_text(wire.get("valid_from"), "valid_from"),
            available_at=_timestamp_text(wire.get("available_at"), "available_at"),
            status=SecurityStatus(_text(wire.get("status"), "status")),
            source_refs=_source_refs(wire.get("source_refs")),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            identity_hash=identity_hash,
            cik=None if wire.get("cik") is None else Cik(_text(wire.get("cik"), "cik")),
            cusip=None if wire.get("cusip") is None else Cusip(_text(wire.get("cusip"), "cusip")),
            isin=None if wire.get("isin") is None else Isin(_text(wire.get("isin"), "isin")),
            valid_to=None if valid_to is None else _timestamp_text(valid_to, "valid_to"),
        )
    except ValueError as error:
        raise PostgresSecuritiesError("stored identity record failed reconstruction") from error


def _event_from_wire(wire: dict[str, object], record_hash: str) -> CorporateActionRecord:
    """Rebuild one lineage row from its stored wire; the constructor re-verifies."""
    ratio = wire.get("ratio")
    if type(ratio) is not dict:
        raise PostgresSecuritiesError("wire ratio must be an object")
    numerator = ratio.get("numerator")
    denominator = ratio.get("denominator")
    if type(numerator) is not int or type(denominator) is not int:
        raise PostgresSecuritiesError("wire ratio numerator and denominator must be integers")
    try:
        record = CorporateActionRecord(
            event_id=_text(wire.get("event_id"), "event_id"),
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            security_identity_hash=_text(
                wire.get("security_identity_hash"), "security_identity_hash"
            ),
            action_type=CorporateActionType(_text(wire.get("action_type"), "action_type")),
            ratio=SplitRatio(
                numerator=numerator,
                denominator=denominator,
            ),
            declared_at=_timestamp_text(wire.get("declared_at"), "declared_at"),
            ex_date=TradingDate.from_isoformat(_text(wire.get("ex_date"), "ex_date")),
            effective_date=TradingDate.from_isoformat(
                _text(wire.get("effective_date"), "effective_date")
            ),
            available_at=_timestamp_text(wire.get("available_at"), "available_at"),
            state=CorporateActionState(_text(wire.get("state"), "state")),
            source_refs=_source_refs(wire.get("source_refs")),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            record_hash=record_hash,
        )
    except ValueError as error:
        raise PostgresSecuritiesError(
            "stored corporate-action record failed reconstruction"
        ) from error
    return record


def _decision_from_wire(wire: dict[str, object], decision_hash: str) -> QuarantineDecision:
    """Rebuild one decision from its stored wire; the constructor re-verifies."""
    reasons = wire.get("reasons")
    event_ids = wire.get("event_ids")
    if type(reasons) is not list or type(event_ids) is not list:
        raise PostgresSecuritiesError("wire reasons and event_ids must be arrays")
    try:
        return QuarantineDecision(
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            symbol_as_of=SecuritySymbol(_text(wire.get("symbol_as_of"), "symbol_as_of")),
            master_version=_text(wire.get("master_version"), "master_version"),
            decision_at=_timestamp_text(wire.get("decision_at"), "decision_at"),
            outcome=QuarantineOutcome(_text(wire.get("outcome"), "outcome")),
            reasons=tuple(QuarantineReason(_text(reason, "reasons entry")) for reason in reasons),
            event_ids=tuple(_text(event_id, "event_ids entry") for event_id in event_ids),
            source_refs=_source_refs(wire.get("source_refs")),
            decision_hash=decision_hash,
        )
    except ValueError as error:
        raise PostgresSecuritiesError("stored quarantine decision failed reconstruction") from error


# Keep the production adapters checked against the application-facing capability boundaries.
_POSTGRES_P4_RECORD_LOG_PORT: type[P4SourceRecordLog] = PostgresP4RecordLog
_POSTGRES_IDENTITY_STORE_PORT: type[SecurityIdentityStore] = PostgresSecurityMaster
_POSTGRES_EVENT_STORE_PORT: type[CorporateActionEventStore] = PostgresSecurityMaster
_POSTGRES_DECISION_STORE_PORT: type[QuarantineDecisionStore] = PostgresSecurityMaster
