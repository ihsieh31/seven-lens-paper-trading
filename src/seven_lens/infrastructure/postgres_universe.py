"""PostgreSQL adapter for P4-C universe snapshots, feature vectors, and candidate sets.

Every write goes through the migration's narrow SECURITY DEFINER functions;
this adapter never writes the append-only tables directly and never updates
or deletes anything.  Every read rebuilds exact domain records from the
stored wire form, and the record constructors' hash verification is the
readback re-verification.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Final

import psycopg
from psycopg.types.json import Jsonb

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.screening.contracts import (
    _PRODUCER_VERSION,
    EVIDENCE_CAP,
    FOCUS_CLOSE_CAP,
    FOCUS_OPEN_CAP,
    MAX_CANDIDATE_SET_BYTES,
    MAX_FEATURE_PRICE_SESSION_DATES,
    MAX_FEATURE_RAW_ITEMS,
    MAX_FEATURE_VECTOR_BYTES,
    MAX_SECTOR_ASSIGNMENT_BYTES,
    QUANT_CAP,
    CandidateEntry,
    CandidateSet,
    CandidateStage,
    FactorStatus,
    FeatureVector,
    RawFeature,
    SectorAssignment,
    _reconstruct_candidate_entry,
    _reconstruct_candidate_set,
    _reconstruct_feature_vector,
    _reconstruct_sector_assignment,
)
from seven_lens.screening.funnel import (
    MAX_CLUSTER_MEMBERS,
    MAX_CLUSTER_RESULT_BYTES,
    MAX_CLUSTER_SOURCE_REFS,
    ClusterResult,
    _reconstruct_cluster_result,
)
from seven_lens.screening.manifests import ClusterStatus
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import SecurityId, SecuritySymbol, SourceRef
from seven_lens.sources.roles import P4SourceFamily
from seven_lens.universe.contracts import (
    _UNIVERSE_SNAPSHOT_READBACK_AUTHORITY,
    MAX_UNIVERSE_SNAPSHOT_BYTES,
    MAX_UNIVERSE_SNAPSHOT_ITEMS,
    UniverseEntry,
    UniverseSnapshot,
    WholeShareFeasibility,
    _reconstruct_universe_snapshot,
)

_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REF_KEYS: Final = {"record_id", "family", "record_hash"}
_UNIVERSE_KEYS: Final = {
    "as_of",
    "known_at",
    "security_master_version",
    "market_snapshot_refs",
    "entries",
    "policy_hash",
    "schema_version",
    "producer_version",
}
_UNIVERSE_ENTRY_KEYS: Final = {
    "security_id",
    "symbol",
    "eligible",
    "reason",
    "identity_hash",
    "master_version",
    "market_snapshot_hash",
    "whole_share_feasibility",
    "quarantine_decision_hash",
    "quarantine_event_ids",
}
_FEATURE_KEYS: Final = {
    "security_id",
    "symbol",
    "universe_hash",
    "manifest_hash",
    "as_of",
    "known_at",
    "status",
    "raw",
    "trend",
    "quality",
    "value",
    "low_risk",
    "composite",
    "missing_reason",
    "schema_version",
    "producer_version",
    "price_session_dates",
}
_RAW_FEATURE_KEYS: Final = {
    "name",
    "value",
    "formula_version",
    "source_refs",
    "security_id",
    "missing_reason",
}
_SECTOR_ASSIGNMENT_KEYS: Final = {
    "security_id",
    "cik",
    "sic",
    "division",
    "source_ref",
    "accession",
    "available_at",
    "taxonomy_version",
    "taxonomy_hash",
}
_CANDIDATE_KEYS: Final = {
    "as_of",
    "known_at",
    "factor_manifest_hash",
    "cluster_manifest_hash",
    "universe_hash",
    "quant",
    "evidence",
    "focus_open",
    "focus_close",
    "policy_hash",
    "producer_version",
    "schema_version",
}
_CANDIDATE_ENTRY_KEYS: Final = {
    "security_id",
    "symbol",
    "composite",
    "trend",
    "quality",
    "value",
    "low_risk",
    "stage",
    "feature_hash",
    "universe_hash",
    "quarantine_decision_hash",
    "sector_assignment_hash",
    "evidence_source_refs",
    "reasons",
}
_CLUSTER_KEYS: Final = {
    "cluster_id",
    "as_of",
    "policy_hash",
    "manifest_hash",
    "members",
    "status",
    "source_refs",
}
_WIRE_LIMITS: Final = {
    "universe snapshot": MAX_UNIVERSE_SNAPSHOT_BYTES,
    "feature vector": MAX_FEATURE_VECTOR_BYTES,
    "sector assignment": MAX_SECTOR_ASSIGNMENT_BYTES,
    "candidate set": MAX_CANDIDATE_SET_BYTES,
    "cluster result": MAX_CLUSTER_RESULT_BYTES,
}


class PostgresUniverseError(RuntimeError):
    """Raised when PostgreSQL rejects a P4-C screening write or read."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class PostgresUniverseSnapshotStore:
    """Append-only universe snapshot authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, snapshot: UniverseSnapshot) -> AppendOutcome:
        if type(snapshot) is not UniverseSnapshot:
            raise ValueError("only an exact UniverseSnapshot can be appended")
        snapshot.verify_integrity()
        wire = snapshot.wire()
        _assert_wire_size(wire, "universe snapshot")
        try:
            row = self._connection.execute(
                "SELECT public.append_universe_snapshot(%s, %s)",
                (snapshot.universe_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("universe snapshot append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresUniverseError("universe snapshot authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, universe_hash: str) -> UniverseSnapshot | None:
        if type(universe_hash) is not str or _HASH_TEXT.fullmatch(universe_hash) is None:
            raise ValueError("universe_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT universe_hash, wire
            FROM public.universe_snapshots
            WHERE universe_hash = %s
            """,
            (universe_hash,),
        ).fetchone()
        if row is None:
            return None
        return _universe_from_wire(_wire(row[1], "universe snapshot"), str(row[0]))

    def latest(self) -> UniverseSnapshot | None:
        row = self._connection.execute(
            """
            SELECT universe_hash, wire
            FROM public.universe_snapshots
            ORDER BY as_of DESC, appended_at DESC, universe_hash DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return _universe_from_wire(_wire(row[1], "universe snapshot"), str(row[0]))

    def snapshots(self) -> tuple[UniverseSnapshot, ...]:
        rows = self._connection.execute(
            """
            SELECT universe_hash, wire
            FROM public.universe_snapshots
            ORDER BY as_of, appended_at, universe_hash
            """
        ).fetchall()
        return tuple(
            _universe_from_wire(_wire(row[1], "universe snapshot"), str(row[0])) for row in rows
        )

    def count(self) -> int:
        row = self._connection.execute("SELECT count(*) FROM public.universe_snapshots").fetchone()
        if row is None or type(row[0]) is not int:
            raise PostgresUniverseError("universe snapshot count returned an invalid result")
        return row[0]


class PostgresFeatureVectorStore:
    """Append-only feature vector authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, vector: FeatureVector) -> AppendOutcome:
        if type(vector) is not FeatureVector:
            raise ValueError("only an exact FeatureVector can be appended")
        vector.verify_integrity()
        wire = vector.wire()
        _assert_wire_size(wire, "feature vector")
        try:
            row = self._connection.execute(
                "SELECT public.append_feature_vector(%s, %s)",
                (vector.feature_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("feature vector append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresUniverseError("feature vector authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, feature_hash: str) -> FeatureVector | None:
        if type(feature_hash) is not str or _HASH_TEXT.fullmatch(feature_hash) is None:
            raise ValueError("feature_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT feature_hash, wire
            FROM public.feature_vectors
            WHERE feature_hash = %s
            """,
            (feature_hash,),
        ).fetchone()
        if row is None:
            return None
        return _feature_from_wire(_wire(row[1], "feature vector"), str(row[0]))

    def vectors_for_as_of(self, as_of: object) -> tuple[FeatureVector, ...]:
        if type(as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        rows = self._connection.execute(
            """
            SELECT feature_hash, wire
            FROM public.feature_vectors
            WHERE as_of = %s
            ORDER BY security_id
            """,
            (as_of.value,),
        ).fetchall()
        return tuple(
            _feature_from_wire(_wire(row[1], "feature vector"), str(row[0])) for row in rows
        )


class PostgresSectorAssignmentStore:
    """Append-only SEC SIC assignment authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, assignment: SectorAssignment) -> AppendOutcome:
        if type(assignment) is not SectorAssignment:
            raise ValueError("only an exact SectorAssignment can be appended")
        assignment.verify_integrity()
        wire = assignment.wire()
        _assert_wire_size(wire, "sector assignment")
        try:
            row = self._connection.execute(
                "SELECT public.append_sector_assignment(%s, %s)",
                (assignment.assignment_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("sector assignment append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresUniverseError("sector assignment authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, assignment_hash: str) -> SectorAssignment | None:
        if type(assignment_hash) is not str or _HASH_TEXT.fullmatch(assignment_hash) is None:
            raise ValueError("assignment_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT assignment_hash, wire
            FROM public.sector_assignments
            WHERE assignment_hash = %s
            """,
            (assignment_hash,),
        ).fetchone()
        if row is None:
            return None
        return _sector_assignment_from_wire(_wire(row[1], "sector assignment"), str(row[0]))


class PostgresCandidateSetStore:
    """Append-only candidate set authority over PostgreSQL."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, candidate_set: CandidateSet) -> AppendOutcome:
        if type(candidate_set) is not CandidateSet:
            raise ValueError("only an exact CandidateSet can be appended")
        candidate_set.verify_integrity()
        wire = candidate_set.wire()
        _assert_wire_size(wire, "candidate set")
        try:
            row = self._connection.execute(
                "SELECT public.append_candidate_set(%s, %s)",
                (candidate_set.candidate_hash, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("candidate set append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresUniverseError("candidate set authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, candidate_hash: str) -> CandidateSet | None:
        if type(candidate_hash) is not str or _HASH_TEXT.fullmatch(candidate_hash) is None:
            raise ValueError("candidate_hash must be a SHA-256 digest")
        row = self._connection.execute(
            """
            SELECT candidate_hash, wire
            FROM public.candidate_sets
            WHERE candidate_hash = %s
            """,
            (candidate_hash,),
        ).fetchone()
        if row is None:
            return None
        return _candidate_from_wire(_wire(row[1], "candidate set"), str(row[0]))

    def latest(self) -> CandidateSet | None:
        row = self._connection.execute(
            """
            SELECT candidate_hash, wire
            FROM public.candidate_sets
            ORDER BY as_of DESC, appended_at DESC, candidate_hash DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return _candidate_from_wire(_wire(row[1], "candidate set"), str(row[0]))


class PostgresClusterResultStore:
    """PostgreSQL authority for content-addressed cluster results."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    def append(self, result: ClusterResult) -> AppendOutcome:
        if type(result) is not ClusterResult:
            raise ValueError("only an exact ClusterResult can be appended")
        result.verify_integrity()
        wire = result.wire()
        _assert_wire_size(wire, "cluster result")
        try:
            row = self._connection.execute(
                "SELECT public.append_cluster_result(%s, %s)",
                (result.cluster_id, Jsonb(wire)),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("cluster result append failed", error) from error
        if row is None or type(row[0]) is not str:
            raise PostgresUniverseError("cluster result authority returned an invalid result")
        return AppendOutcome(row[0])

    def get(self, cluster_id: str) -> ClusterResult | None:
        if type(cluster_id) is not str or _HASH_TEXT.fullmatch(cluster_id) is None:
            raise ValueError("cluster_id must be a SHA-256 digest")
        rows = self._connection.execute(
            """
            SELECT cluster_id, wire, security_id
            FROM public.cluster_results
            WHERE cluster_id = %s
            ORDER BY ordinal
            """,
            (cluster_id,),
        ).fetchall()
        if not rows:
            return None
        return _cluster_from_rows(rows, cluster_id)

    def results_for_as_of(self, as_of: object) -> tuple[ClusterResult, ...]:
        if type(as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        rows = self._connection.execute(
            """
            SELECT cluster_id, wire, security_id
            FROM public.cluster_results
            WHERE as_of = %s
            ORDER BY cluster_id, ordinal
            """,
            (as_of.value,),
        ).fetchall()
        results: list[ClusterResult] = []
        current_id: str | None = None
        current_rows: list[tuple[object, ...]] = []
        for row in rows:
            row_id = str(row[0])
            if current_id is not None and row_id != current_id:
                results.append(_cluster_from_rows(current_rows, current_id))
                current_rows = []
            current_id = row_id
            current_rows.append(row)
        if current_id is not None:
            results.append(_cluster_from_rows(current_rows, current_id))
        return tuple(results)

    def count(self) -> int:
        row = self._connection.execute(
            "SELECT count(DISTINCT cluster_id) FROM public.cluster_results"
        ).fetchone()
        if row is None or type(row[0]) is not int:
            raise PostgresUniverseError("cluster result count returned an invalid result")
        return row[0]


def _wire(value: object, operation: str) -> dict[str, object]:
    if type(value) is not dict:
        raise PostgresUniverseError(f"{operation} wire form must be a JSON object")
    _assert_wire_size(value, operation)
    return value


def _assert_wire_size(wire: dict[str, object], operation: str) -> None:
    """Apply the exact canonical UTF-8 resource bound enforced by migration 0024."""
    limit = _WIRE_LIMITS.get(operation)
    if limit is None:
        raise PostgresUniverseError(f"unknown P4-C wire operation: {operation}")
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
        raise PostgresUniverseError(f"{operation} wire is not canonical JSON") from error
    if size > limit:
        raise PostgresUniverseError(f"{operation} wire exceeds the {limit}-byte limit")


def _translate(message: str, error: psycopg.Error) -> PostgresUniverseError:
    sqlstate = error.sqlstate
    if sqlstate == "40001":
        message = f"{message}: concurrent transition lost"
    elif sqlstate == "23514":
        message = f"{message}: storage constraint violated"
    return PostgresUniverseError(message, sqlstate=sqlstate)


def _text(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise PostgresUniverseError(f"wire {field_name} must be text")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is not None and type(value) is not str:
        raise PostgresUniverseError(f"wire {field_name} must be text or null")
    return value


def _timestamp_text(value: object, field_name: str) -> UtcTimestamp:
    return UtcTimestamp.from_isoformat(_text(value, field_name))


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not str:
        raise PostgresUniverseError(f"wire {field_name} must be a decimal string")
    try:
        return Decimal(value)
    except Exception as error:
        raise PostgresUniverseError(f"wire {field_name} is not a valid decimal") from error


def _bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise PostgresUniverseError(f"wire {field_name} must be a boolean")
    return value


def _universe_from_wire(wire: dict[str, object], universe_hash: str) -> UniverseSnapshot:
    if set(wire) != _UNIVERSE_KEYS:
        raise PostgresUniverseError("universe wire has an unexpected shape")
    entries = wire.get("entries")
    if type(entries) is not list:
        raise PostgresUniverseError("universe wire entries must be an array")
    if len(entries) > MAX_UNIVERSE_SNAPSHOT_ITEMS:
        raise PostgresUniverseError("universe wire entries exceed their item bound")
    entries_tuple = tuple(_universe_entry(item) for item in entries)
    refs = wire.get("market_snapshot_refs")
    if type(refs) is not list:
        raise PostgresUniverseError("universe wire market_snapshot_refs must be an array")
    if len(refs) > MAX_UNIVERSE_SNAPSHOT_ITEMS:
        raise PostgresUniverseError("universe wire market_snapshot_refs exceed their item bound")
    refs_tuple = tuple(_text(item, "market_snapshot_refs.item") for item in refs)
    try:
        return _reconstruct_universe_snapshot(
            authority=_UNIVERSE_SNAPSHOT_READBACK_AUTHORITY,
            as_of=TradingDate.from_isoformat(_text(wire.get("as_of"), "as_of")),
            known_at=_timestamp_text(wire.get("known_at"), "known_at"),
            security_master_version=_text(
                wire.get("security_master_version"), "security_master_version"
            ),
            market_snapshot_refs=refs_tuple,
            entries=entries_tuple,
            policy_hash=_text(wire.get("policy_hash"), "policy_hash"),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            producer_version=_text(wire.get("producer_version"), "producer_version"),
            universe_hash=universe_hash,
        )
    except ValueError as error:
        raise PostgresUniverseError("stored universe snapshot failed reconstruction") from error


def _universe_entry(item: object) -> UniverseEntry:
    if type(item) is not dict:
        raise PostgresUniverseError("universe wire entries must be objects")
    if set(item) != _UNIVERSE_ENTRY_KEYS:
        raise PostgresUniverseError("universe wire entry has an unexpected shape")
    try:
        return UniverseEntry(
            security_id=SecurityId(_text(item.get("security_id"), "entry.security_id")),
            symbol=SecuritySymbol(_text(item.get("symbol"), "entry.symbol")),
            eligible=_bool(item.get("eligible"), "entry.eligible"),
            reason=(
                None
                if item.get("reason") is None
                else ClosedReason(_text(item.get("reason"), "entry.reason"))
            ),
            identity_hash=_optional_text(item.get("identity_hash"), "entry.identity_hash"),
            master_version=_optional_text(item.get("master_version"), "entry.master_version"),
            market_snapshot_hash=_optional_text(
                item.get("market_snapshot_hash"), "entry.market_snapshot_hash"
            ),
            whole_share_feasibility=WholeShareFeasibility(
                _text(item.get("whole_share_feasibility"), "entry.whole_share_feasibility")
            ),
            quarantine_decision_hash=_optional_text(
                item.get("quarantine_decision_hash"), "entry.quarantine_decision_hash"
            ),
            quarantine_event_ids=_string_tuple(
                item.get("quarantine_event_ids"), "entry.quarantine_event_ids"
            ),
        )
    except ValueError as error:
        raise PostgresUniverseError("stored universe entry failed reconstruction") from error


def _feature_from_wire(wire: dict[str, object], feature_hash: str) -> FeatureVector:
    if set(wire) != _FEATURE_KEYS:
        raise PostgresUniverseError("feature wire has an unexpected shape")
    producer_version = _text(wire.get("producer_version"), "producer_version")
    if producer_version != _PRODUCER_VERSION:
        raise PostgresUniverseError("stored feature vector producer_version is not approved")
    raw = wire.get("raw")
    if type(raw) is not list:
        raise PostgresUniverseError("feature wire raw must be an array")
    if len(raw) != MAX_FEATURE_RAW_ITEMS:
        raise PostgresUniverseError(
            f"feature wire raw must contain exactly {MAX_FEATURE_RAW_ITEMS} items"
        )
    raw_tuple = tuple(_raw_feature(item) for item in raw)
    price_session_dates = wire.get("price_session_dates")
    if type(price_session_dates) is not list:
        raise PostgresUniverseError("feature wire price_session_dates must be an array")
    if len(price_session_dates) > MAX_FEATURE_PRICE_SESSION_DATES:
        raise PostgresUniverseError("feature wire price_session_dates exceed their item bound")
    try:
        return _reconstruct_feature_vector(
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            symbol=SecuritySymbol(_text(wire.get("symbol"), "symbol")),
            universe_hash=_text(wire.get("universe_hash"), "universe_hash"),
            manifest_hash=_text(wire.get("manifest_hash"), "manifest_hash"),
            as_of=_timestamp_text(wire.get("as_of"), "as_of"),
            known_at=_timestamp_text(wire.get("known_at"), "known_at"),
            status=FactorStatus(_text(wire.get("status"), "status")),
            raw=raw_tuple,
            trend=None if wire.get("trend") is None else _decimal(wire.get("trend"), "trend"),
            quality=(
                None if wire.get("quality") is None else _decimal(wire.get("quality"), "quality")
            ),
            value=None if wire.get("value") is None else _decimal(wire.get("value"), "value"),
            low_risk=(
                None if wire.get("low_risk") is None else _decimal(wire.get("low_risk"), "low_risk")
            ),
            composite=(
                None
                if wire.get("composite") is None
                else _decimal(wire.get("composite"), "composite")
            ),
            missing_reason=_optional_text(wire.get("missing_reason"), "missing_reason"),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            feature_hash=feature_hash,
            price_session_dates=tuple(
                TradingDate.from_isoformat(_text(value, "price_session_dates.item"))
                for value in price_session_dates
            ),
        )
    except ValueError as error:
        raise PostgresUniverseError("stored feature vector failed reconstruction") from error


def _raw_feature(item: object) -> RawFeature:
    if type(item) is not dict:
        raise PostgresUniverseError("feature wire raw items must be objects")
    if set(item) != _RAW_FEATURE_KEYS:
        raise PostgresUniverseError("feature wire raw item has an unexpected shape")
    try:
        return RawFeature(
            name=_text(item.get("name"), "raw.name"),
            value=(None if item.get("value") is None else _decimal(item.get("value"), "raw.value")),
            formula_version=_text(item.get("formula_version"), "raw.formula_version"),
            source_refs=_source_refs(item.get("source_refs"), max_items=64),
            security_id=SecurityId(_text(item.get("security_id"), "raw.security_id")),
            missing_reason=_optional_text(item.get("missing_reason"), "raw.missing_reason"),
        )
    except (TypeError, ValueError) as error:
        raise PostgresUniverseError("stored raw feature failed reconstruction") from error


def _source_refs(value: object, *, max_items: int | None = None) -> tuple[SourceRef, ...]:
    if type(value) is not list:
        raise PostgresUniverseError("feature wire raw.source_refs must be an array")
    if max_items is not None and len(value) > max_items:
        raise PostgresUniverseError("wire source_refs exceeds its item bound")
    refs: list[SourceRef] = []
    for item in value:
        refs.append(_source_ref(item, "raw.source_refs"))
    return tuple(refs)


def _source_ref(value: object, field_name: str) -> SourceRef:
    if type(value) is not dict or set(value) != _SOURCE_REF_KEYS:
        raise PostgresUniverseError(f"wire {field_name} must have an exact source-reference shape")
    try:
        return SourceRef(
            record_id=_text(value.get("record_id"), f"{field_name}.record_id"),
            family=P4SourceFamily(_text(value.get("family"), f"{field_name}.family")),
            record_hash=_text(value.get("record_hash"), f"{field_name}.record_hash"),
        )
    except ValueError as error:
        raise PostgresUniverseError(f"stored {field_name} failed reconstruction") from error


def _sector_assignment_from_wire(wire: dict[str, object], assignment_hash: str) -> SectorAssignment:
    if set(wire) != _SECTOR_ASSIGNMENT_KEYS:
        raise PostgresUniverseError("sector assignment wire has an unexpected shape")
    try:
        return _reconstruct_sector_assignment(
            security_id=SecurityId(_text(wire.get("security_id"), "security_id")),
            cik=_text(wire.get("cik"), "cik"),
            sic=_text(wire.get("sic"), "sic"),
            division=_text(wire.get("division"), "division"),
            source_ref=_source_ref(wire.get("source_ref"), "source_ref"),
            accession=_optional_text(wire.get("accession"), "accession"),
            available_at=_timestamp_text(wire.get("available_at"), "available_at"),
            taxonomy_version=_text(wire.get("taxonomy_version"), "taxonomy_version"),
            taxonomy_hash=_text(wire.get("taxonomy_hash"), "taxonomy_hash"),
            assignment_hash=assignment_hash,
        )
    except ValueError as error:
        raise PostgresUniverseError("stored sector assignment failed reconstruction") from error


def _candidate_from_wire(wire: dict[str, object], candidate_hash: str) -> CandidateSet:
    if set(wire) != _CANDIDATE_KEYS:
        raise PostgresUniverseError("candidate wire has an unexpected shape")
    try:
        return _reconstruct_candidate_set(
            as_of=_timestamp_text(wire.get("as_of"), "as_of"),
            known_at=_timestamp_text(wire.get("known_at"), "known_at"),
            factor_manifest_hash=_text(wire.get("factor_manifest_hash"), "factor_manifest_hash"),
            cluster_manifest_hash=_text(wire.get("cluster_manifest_hash"), "cluster_manifest_hash"),
            universe_hash=_text(wire.get("universe_hash"), "universe_hash"),
            quant=_entries(wire.get("quant"), CandidateStage.QUANT),
            evidence=_entries(wire.get("evidence"), CandidateStage.EVIDENCE),
            focus_open=_entries(wire.get("focus_open"), CandidateStage.FOCUS_OPEN),
            focus_close=_entries(wire.get("focus_close"), CandidateStage.FOCUS_CLOSE),
            policy_hash=_text(wire.get("policy_hash"), "policy_hash"),
            producer_version=_text(wire.get("producer_version"), "producer_version"),
            schema_version=SchemaVersion(_text(wire.get("schema_version"), "schema_version")),
            candidate_hash=candidate_hash,
        )
    except ValueError as error:
        raise PostgresUniverseError("stored candidate set failed reconstruction") from error


def _cluster_from_rows(rows: list[tuple[object, ...]], cluster_id: str) -> ClusterResult:
    if not rows:
        raise PostgresUniverseError("stored cluster result has no rows")
    wires = [_wire(row[1], "cluster result") for row in rows]
    if any(wire != wires[0] for wire in wires[1:]):
        raise PostgresUniverseError("stored cluster result rows disagree on their wire")
    try:
        result = _cluster_from_wire(wires[0])
    except ValueError as error:
        raise PostgresUniverseError("stored cluster result failed reconstruction") from error
    if result.cluster_id != cluster_id:
        raise PostgresUniverseError("stored cluster result id does not match its wire")
    stored_members = tuple(str(row[2]) for row in rows)
    if stored_members != tuple(member.value for member in result.members):
        raise PostgresUniverseError("stored cluster result members do not match its wire")
    return result


def _cluster_from_wire(wire: dict[str, object]) -> ClusterResult:
    if set(wire) != _CLUSTER_KEYS:
        raise PostgresUniverseError("cluster wire has an unexpected shape")
    members = wire.get("members")
    if type(members) is not list:
        raise PostgresUniverseError("cluster wire members must be an array")
    if len(members) > MAX_CLUSTER_MEMBERS:
        raise PostgresUniverseError("cluster wire members exceed their item bound")
    source_refs = wire.get("source_refs")
    if type(source_refs) is not list:
        raise PostgresUniverseError("cluster wire source_refs must be an array")
    if len(source_refs) > MAX_CLUSTER_SOURCE_REFS:
        raise PostgresUniverseError("cluster wire source_refs exceed their item bound")
    try:
        return _reconstruct_cluster_result(
            cluster_id=_text(wire.get("cluster_id"), "cluster_id"),
            as_of=_timestamp_text(wire.get("as_of"), "as_of"),
            policy_hash=_text(wire.get("policy_hash"), "policy_hash"),
            manifest_hash=_text(wire.get("manifest_hash"), "manifest_hash"),
            members=tuple(SecurityId(_text(member, "members.item")) for member in members),
            status=ClusterStatus(_text(wire.get("status"), "status")),
            source_refs=_source_refs(source_refs, max_items=MAX_CLUSTER_SOURCE_REFS),
        )
    except ValueError as error:
        raise PostgresUniverseError("stored cluster result failed reconstruction") from error


def _entries(value: object, stage: CandidateStage) -> tuple[CandidateEntry, ...]:
    if type(value) is not list:
        raise PostgresUniverseError("candidate wire stage must be an array")
    stage_limits = {
        CandidateStage.QUANT: QUANT_CAP,
        CandidateStage.EVIDENCE: EVIDENCE_CAP,
        CandidateStage.FOCUS_OPEN: FOCUS_OPEN_CAP,
        CandidateStage.FOCUS_CLOSE: FOCUS_CLOSE_CAP,
    }
    if len(value) > stage_limits[stage]:
        raise PostgresUniverseError("candidate wire stage exceeds its item bound")
    result: list[CandidateEntry] = []
    for item in value:
        if type(item) is not dict:
            raise PostgresUniverseError("candidate wire entries must be objects")
        if set(item) != _CANDIDATE_ENTRY_KEYS:
            raise PostgresUniverseError("candidate wire entry has an unexpected shape")
        reasons = item.get("reasons")
        if type(reasons) is not list:
            raise PostgresUniverseError("candidate wire entry reasons must be an array")
        reasons_tuple = tuple(ClosedReason(_text(r, "entry.reasons.item")) for r in reasons)
        evidence_source_refs = _source_refs(item.get("evidence_source_refs"), max_items=64)
        entry_stage = CandidateStage(_text(item.get("stage"), "entry.stage"))
        if entry_stage is not stage:
            raise PostgresUniverseError("candidate wire entry stage does not match its array")
        result.append(
            _reconstruct_candidate_entry(
                security_id=SecurityId(_text(item.get("security_id"), "entry.security_id")),
                symbol=SecuritySymbol(_text(item.get("symbol"), "entry.symbol")),
                composite=_decimal(item.get("composite"), "entry.composite"),
                trend=_decimal(item.get("trend"), "entry.trend"),
                quality=_decimal(item.get("quality"), "entry.quality"),
                value=_decimal(item.get("value"), "entry.value"),
                low_risk=_decimal(item.get("low_risk"), "entry.low_risk"),
                stage=entry_stage,
                feature_hash=_text(item.get("feature_hash"), "entry.feature_hash"),
                universe_hash=_text(item.get("universe_hash"), "entry.universe_hash"),
                quarantine_decision_hash=_text(
                    item.get("quarantine_decision_hash"), "entry.quarantine_decision_hash"
                ),
                sector_assignment_hash=_optional_text(
                    item.get("sector_assignment_hash"), "entry.sector_assignment_hash"
                ),
                evidence_source_refs=evidence_source_refs,
                reasons=reasons_tuple,
            )
        )
    return tuple(result)


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) is not list or any(type(item) is not str for item in value):
        raise PostgresUniverseError(f"wire {field_name} must be an array of strings")
    return tuple(value)
