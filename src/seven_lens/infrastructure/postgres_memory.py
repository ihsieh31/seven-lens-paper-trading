"""PostgreSQL adapter for immutable P3-F reflection and memory lineage."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.infrastructure.content_store import FileContentStore, StoredContent
from seven_lens.memory.contracts import (
    ArtifactState,
    DailyReflectionRecord,
    FactKind,
    FactRef,
    MemoryArtifact,
    MemoryCategory,
    MemoryEntry,
    ObservationKind,
    ReflectionObservation,
    ReflectionSourceRef,
)
from seven_lens.memory.curation import (
    CurationAuditRecord,
    CurationPipeline,
    CurationPreparation,
    CurationRequest,
    curation_input_hash,
)
from seven_lens.memory.template import CURATION_TEMPLATE_HASH
from seven_lens.memory.validation import MemoryValidator, ValidationResult

_HASH = re.compile(r"^[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")


class PostgresMemoryError(RuntimeError):
    """Raised when PostgreSQL rejects memory identity, lineage, or promotion."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


class PostgresMemoryRepository:
    """Adapter whose writes can use only the migration's narrow authority functions."""

    def __init__(self, connection: psycopg.Connection[Any]) -> None:
        self._connection = connection

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run a memory promotion unit in one connection transaction/savepoint."""
        with self._connection.transaction():
            yield

    def append_reflection(self, record: DailyReflectionRecord) -> None:
        if type(record) is not DailyReflectionRecord:
            raise ValueError("only an exact DailyReflectionRecord can be appended")
        record.verify_integrity()
        corrections = {
            item.supersedes_record_id
            for item in record.observations
            if item.kind is ObservationKind.CORRECTION
        }
        corrections.discard(None)
        if len(corrections) > 1:
            raise ValueError("one reflection cannot supersede multiple records")
        superseded = next(iter(corrections), None)
        content = _canonical_bytes(record.content_wire())
        try:
            self._connection.execute(
                """
                SELECT public.register_reflection_record(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s
                )
                """,
                (
                    record.record_id,
                    record.schema_version,
                    "CORRECTION" if superseded is not None else "DAILY",
                    record.created_at.value,
                    record.available_at.value,
                    record.as_of.value,
                    record.cutoff_at.value,
                    record.proposal_id,
                    record.decision_id,
                    record.research_bundle_hash,
                    record.portfolio_snapshot_hash,
                    record.content_hash,
                    content,
                    record.prompt_version,
                    record.model_version,
                    record.provider_version,
                    record.data_version,
                    record.memory_version,
                    [item.source_id for item in record.sources],
                    [item.source_type for item in record.sources],
                    [item.content_hash for item in record.sources],
                    [item.available_at.value for item in record.sources],
                    superseded,
                    "SOURCE_CORRECTION" if superseded is not None else None,
                ),
            )
        except psycopg.Error as error:
            raise _translate("reflection append failed", error) from error

    # Compatibility with the reflection pipeline's narrower repository port.
    append = append_reflection

    def get(self, record_id: str) -> DailyReflectionRecord | None:
        if type(record_id) is not str or not record_id:
            raise ValueError("record_id must be non-empty text")
        row = self._connection.execute(
            "SELECT content_hash, content_bytes FROM public.approved_reflection_records "
            "WHERE reflection_id = %s",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return _reflection_from_bytes(bytes(row[1]), str(row[0]))

    def load_reflections(self, cutoff: UtcTimestamp) -> tuple[DailyReflectionRecord, ...]:
        if type(cutoff) is not UtcTimestamp:
            raise ValueError("cutoff must be an exact UtcTimestamp")
        rows = self._connection.execute(
            """
            SELECT record.content_hash, record.content_bytes
            FROM public.approved_reflection_records AS record
            WHERE record.available_at <= %s AND record.cutoff_at <= %s
              AND NOT EXISTS (
                  SELECT 1
                  FROM public.approved_reflection_records AS correction
                  WHERE correction.superseded_reflection_id = record.reflection_id
                    AND correction.created_at <= %s
                    AND correction.available_at <= %s
                    AND correction.cutoff_at <= %s
              )
            ORDER BY record.available_at, record.reflection_id
            """,
            (cutoff.value, cutoff.value, cutoff.value, cutoff.value, cutoff.value),
        ).fetchall()
        return tuple(_reflection_from_bytes(bytes(row[1]), str(row[0])) for row in rows)

    def register_candidate(
        self,
        artifact: MemoryArtifact,
        cas_hash: str,
        byte_count: int,
    ) -> None:
        if type(artifact) is not MemoryArtifact or artifact.state is not ArtifactState.CANDIDATE:
            raise ValueError("only an exact candidate MemoryArtifact can be registered")
        artifact.verify_integrity()
        content = artifact.canonical_content_bytes()
        if (
            type(cas_hash) is not str
            or cas_hash != artifact.content_hash
            or type(byte_count) is not int
            or byte_count != len(content)
        ):
            raise ValueError("CAS hash/byte count does not match canonical artifact bytes")
        try:
            self._connection.execute(
                """
                SELECT public.register_memory_candidate(
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                (
                    artifact.artifact_id,
                    artifact.schema_version,
                    artifact.created_at.value,
                    artifact.cutoff_at.value,
                    artifact.previous_artifact_id,
                    artifact.content_hash,
                    cas_hash,
                    content,
                    byte_count,
                    artifact.line_count,
                    len(artifact.entries),
                    artifact.prompt_version,
                    artifact.model_version,
                    artifact.provider_version,
                    list(artifact.source_record_ids),
                ),
            )
        except psycopg.Error as error:
            raise _translate("memory candidate registration failed", error) from error

    def append_curation_audit(self, record: CurationAuditRecord) -> bool:
        """Append bounded curation metadata through the curator-only DB authority function."""
        if type(record) is not CurationAuditRecord:
            raise ValueError("only an exact CurationAuditRecord can be appended")
        record.verify_integrity()
        parameters = record.db_parameters()
        try:
            row = self._connection.execute(
                """
                SELECT public.register_memory_curation_audit(
                    %s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                """,
                parameters,
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("memory curation audit append failed", error) from error
        if row is None or type(row[0]) is not bool:
            raise PostgresMemoryError("memory curation audit authority returned invalid result")
        return row[0]

    def mark_validated(
        self,
        result: ValidationResult,
        validation_report_hash: str,
        validator_version: str,
    ) -> None:
        if type(result) is not ValidationResult or not result.valid:
            raise ValueError("only an exact successful deterministic ValidationResult is accepted")
        artifact = result.artifact
        artifact.verify_integrity()
        if _HASH.fullmatch(validation_report_hash) is None:
            raise ValueError("validation_report_hash must be a lowercase SHA-256 digest")
        if type(validator_version) is not str or _VERSION.fullmatch(validator_version) is None:
            raise ValueError("validator_version must use canonical bounded version text")
        try:
            self._connection.execute(
                "SELECT public.validate_memory_artifact(%s,%s,%s,%s)",
                (
                    artifact.artifact_id,
                    artifact.content_hash,
                    validator_version,
                    validation_report_hash,
                ),
            )
        except psycopg.Error as error:
            raise _translate("memory validation transition failed", error) from error

    def promote(self, artifact_id: str, requested_as_of: UtcTimestamp) -> bool:
        if type(requested_as_of) is not UtcTimestamp:
            raise ValueError("requested_as_of must be an exact UtcTimestamp")
        current = self.current_at(requested_as_of)
        expected = None if current is None else current.artifact_id
        try:
            promoted = self._connection.execute(
                "SELECT public.promote_memory_artifact(%s,%s,%s)",
                (artifact_id, expected, requested_as_of.value),
            ).fetchone()
        except psycopg.Error as error:
            raise _translate("atomic memory promotion failed", error) from error
        return bool(promoted is not None and promoted[0])

    def current_at(self, as_of: UtcTimestamp) -> MemoryArtifact | None:
        if type(as_of) is not UtcTimestamp:
            raise ValueError("as_of must be an exact UtcTimestamp")
        row = self._connection.execute(
            "SELECT content_hash, content_bytes FROM public.current_memory_artifact(%s)",
            (as_of.value,),
        ).fetchone()
        if row is None:
            return None
        return _artifact_from_bytes(bytes(row[1]), str(row[0]))

    def current_pointer(self) -> MemoryArtifact | None:
        """Read the exact current pointer, independent of historical as-of visibility."""
        row = self._connection.execute(
            "SELECT content_hash, content_bytes FROM public.current_memory_pointer_artifact()"
        ).fetchone()
        if row is None:
            return None
        return _artifact_from_bytes(bytes(row[1]), str(row[0]))

    def database_now(self) -> UtcTimestamp:
        """Read the PostgreSQL clock after registration/validation in this transaction."""
        row = self._connection.execute("SELECT pg_catalog.clock_timestamp()").fetchone()
        if row is None:
            raise PostgresMemoryError("database clock readback failed")
        try:
            return UtcTimestamp(row[0])
        except ValueError as error:
            raise PostgresMemoryError("database clock returned an invalid timestamp") from error


class PostgresMemoryPromotionCoordinator:
    """Production promotion path joining exact FileContentStore bytes to PG authority."""

    def __init__(
        self,
        repository: PostgresMemoryRepository,
        content_store: FileContentStore,
        validator: MemoryValidator,
        *,
        validator_version: str = "p3f.validator.1",
    ) -> None:
        if type(content_store) is not FileContentStore:
            raise ValueError("promotion requires the trusted exact FileContentStore capability")
        if type(validator) is not MemoryValidator:
            raise ValueError("promotion requires the exact deterministic MemoryValidator")
        if type(validator_version) is not str or _VERSION.fullmatch(validator_version) is None:
            raise ValueError("validator_version must use canonical bounded version text")
        self._repository = repository
        self._content_store = content_store
        self._validator = validator
        self._validator_version = validator_version

    def validate_and_promote(
        self,
        preparation: CurationPreparation,
        *,
        source_records: dict[str, DailyReflectionRecord],
        requested_cutoff: UtcTimestamp,
        requested_as_of: UtcTimestamp,
    ) -> ValidationResult:
        if type(preparation) is not CurationPreparation:
            raise ValueError("promotion requires an exact curation preparation")
        preparation.__post_init__()
        artifact = preparation.artifact
        curation_audit = preparation.audit
        if type(artifact) is not MemoryArtifact or artifact.state is not ArtifactState.CANDIDATE:
            raise ValueError("promotion requires an exact candidate MemoryArtifact")
        artifact.verify_integrity()
        if type(source_records) is not dict or type(requested_cutoff) is not UtcTimestamp:
            raise ValueError("promotion validation context is invalid")
        if type(requested_as_of) is not UtcTimestamp:
            raise ValueError("requested_as_of must be an exact UtcTimestamp")
        if requested_as_of.value < requested_cutoff.value:
            raise ValueError("requested_as_of cannot precede requested_cutoff")
        if type(curation_audit) is not CurationAuditRecord:
            raise ValueError("promotion requires an exact curation audit record")
        curation_audit.verify_integrity()
        if curation_audit.artifact_id != artifact.artifact_id:
            raise ValueError("curation audit artifact identity does not match candidate")
        if (
            curation_audit.audit_kind != "MODEL"
            or curation_audit.template_hash != CURATION_TEMPLATE_HASH
        ):
            raise ValueError("curation audit package identity is invalid")
        if set(source_records) != set(artifact.source_record_ids):
            raise ValueError("promotion source lineage does not match prepared artifact")
        ordered_sources = tuple(source_records[item] for item in artifact.source_record_ids)
        expected_input_hash = curation_input_hash(
            CurationRequest(requested_cutoff, ordered_sources)
        )
        if curation_audit.input_hash != expected_input_hash:
            raise ValueError("curation audit input hash does not match source lineage")
        content = artifact.canonical_content_bytes()
        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash != artifact.content_hash:
            raise RuntimeError("artifact bytes failed exact hash verification")

        result = self._validator.validate(
            artifact,
            source_records=source_records,
            requested_cutoff=requested_cutoff,
        )
        expected_outcome = "SUCCESS" if result.valid else "ABSTAIN"
        expected_report_hash = CurationPipeline._report_hash(
            input_hash=curation_audit.input_hash,
            output_hash=artifact.content_hash,
            outcome=expected_outcome,
            issues=tuple((item.stage, item.code) for item in result.issues),
            template_hash=curation_audit.template_hash,
        )
        if (
            tuple((item.stage, item.code) for item in result.issues)
            != tuple((item.stage, item.code) for item in preparation.issues)
            or curation_audit.case_count != preparation.case_count
            or curation_audit.output_hash != artifact.content_hash
            or curation_audit.outcome != expected_outcome
            or curation_audit.report_hash != expected_report_hash
            or curation_audit.accepted_count
            != (len(result.artifact.entries) if result.valid else 0)
        ):
            raise ValueError("curation audit metadata does not match deterministic validation")

        with self._repository.transaction():
            stored = self._content_store.put(content, declared_hash=artifact.content_hash)
            readback = self._content_store.get(artifact.content_hash)
            if (
                type(stored) is not StoredContent
                or type(readback) is not bytes
                or readback != content
                or hashlib.sha256(readback).hexdigest() != artifact.content_hash
                or stored.content_hash != artifact.content_hash
                or stored.size != len(content)
            ):
                raise RuntimeError("staged memory bytes failed exact verification")

            # Every DB transition uses metadata derived from the immutable artifact/result. The
            # caller cannot supply a CAS boolean, byte count, validator result, or report hash.
            self._repository.register_candidate(
                artifact,
                artifact.content_hash,
                len(content),
            )
            self._repository.append_curation_audit(curation_audit)
            if not result.valid:
                return result
            report_hash = hashlib.sha256(
                _canonical_bytes(
                    {
                        "artifact_hash": result.artifact.content_hash,
                        "issues": [(item.stage, item.code) for item in result.issues],
                        "validator_version": self._validator_version,
                    }
                )
            ).hexdigest()
            self._repository.mark_validated(
                result,
                report_hash,
                self._validator_version,
            )
            self._repository.promote(artifact.artifact_id, requested_as_of)
            current = self._repository.current_pointer()
            if (
                type(current) is not MemoryArtifact
                or current.artifact_id != artifact.artifact_id
                or current.content_hash != artifact.content_hash
                or current.canonical_content_bytes() != content
            ):
                raise RuntimeError("promoted memory readback failed exact verification")
            return result


# Name retained as a discoverable adapter for callers using the shorter service terminology.
PostgresMemoryPromoter = PostgresMemoryPromotionCoordinator


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8", errors="strict")


def _utc(value: object) -> UtcTimestamp:
    if type(value) is not str:
        raise PostgresMemoryError("persisted memory timestamp is invalid")
    return UtcTimestamp.from_isoformat(value)


def _reflection_from_bytes(content: bytes, content_hash: str) -> DailyReflectionRecord:
    try:
        value = json.loads(content)
        sources = tuple(
            ReflectionSourceRef(
                source_id=item["source_id"],
                source_type=item["source_type"],
                content_hash=item["content_hash"],
                available_at=_utc(item["available_at"]),
                facts=tuple(
                    FactRef(fact["fact_id"], FactKind(fact["kind"]), fact["value"])
                    for fact in item["facts"]
                ),
                prompt_injection_flags=tuple(item["prompt_injection_flags"]),
            )
            for item in value["sources"]
        )
        observations = tuple(
            ReflectionObservation(
                kind=ObservationKind(item["kind"]),
                observation=item["observation"],
                reusable_lesson=item["reusable_lesson"],
                applies_when=tuple(item["applies_when"]),
                invalid_when=tuple(item["invalid_when"]),
                fact_ids=tuple(item["fact_ids"]),
                supersedes_record_id=item["supersedes_record_id"],
            )
            for item in value["observations"]
        )
        return DailyReflectionRecord(
            record_id=value["record_id"],
            schema_version=value["schema_version"],
            created_at=_utc(value["created_at"]),
            available_at=_utc(value["available_at"]),
            as_of=_utc(value["as_of"]),
            cutoff_at=_utc(value["cutoff_at"]),
            proposal_id=value["proposal_id"],
            decision_id=value["decision_id"],
            research_bundle_hash=value["research_bundle_hash"],
            portfolio_snapshot_hash=value["portfolio_snapshot_hash"],
            sources=sources,
            observations=observations,
            prompt_version=value["prompt_version"],
            model_version=value["model_version"],
            provider_version=value["provider_version"],
            data_version=value["data_version"],
            memory_version=value["memory_version"],
            content_hash=content_hash,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PostgresMemoryError("persisted reflection bytes are invalid") from error


def _artifact_from_bytes(content: bytes, content_hash: str) -> MemoryArtifact:
    try:
        value = json.loads(content)
        entries = tuple(
            MemoryEntry(
                category=MemoryCategory(item["category"]),
                importance=item["importance"],
                observation=item["observation"],
                reusable_lesson=item["reusable_lesson"],
                applies_when=tuple(item["applies_when"]),
                invalid_when=tuple(item["invalid_when"]),
                evidence_ids=tuple(item["evidence_ids"]),
                source_record_ids=tuple(item["source_record_ids"]),
                risk_reason_codes=tuple(item["risk_reason_codes"]),
            )
            for item in value["entries"]
        )
        return MemoryArtifact(
            artifact_id=value["artifact_id"],
            schema_version=value["schema_version"],
            created_at=_utc(value["created_at"]),
            cutoff_at=_utc(value["cutoff_at"]),
            source_record_ids=tuple(value["source_record_ids"]),
            previous_artifact_id=value["previous_artifact_id"],
            entries=entries,
            line_count=value["line_count"],
            content_hash=content_hash,
            prompt_version=value["prompt_version"],
            model_version=value["model_version"],
            provider_version=value["provider_version"],
            state=ArtifactState.CURRENT,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise PostgresMemoryError("persisted memory artifact bytes are invalid") from error


def _translate(message: str, error: psycopg.Error) -> PostgresMemoryError:
    sqlstate = error.sqlstate
    if sqlstate == "23505":
        message = f"{message}: identity collision"
    elif sqlstate == "40001":
        message = f"{message}: concurrent promotion lost"
    return PostgresMemoryError(message, sqlstate=sqlstate)
