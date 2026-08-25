"""Provider-neutral P3-F curation seam bound to the package-owned template."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Final, Protocol
from uuid import UUID, uuid5

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.memory.contracts import (
    MAX_ARTIFACT_SOURCES,
    ArtifactState,
    DailyReflectionRecord,
    MemoryArtifact,
)
from seven_lens.memory.selection import MemoryCandidate, build_selected_artifact
from seven_lens.memory.template import (
    CURATION_TEMPLATE,
    CURATION_TEMPLATE_HASH,
    CURATION_TEMPLATE_ID,
    CURATION_TEMPLATE_VERSION,
)
from seven_lens.memory.validation import MemoryValidator, ValidationIssue, ValidationResult

MAX_CURATION_NODES: Final = 32_768
MAX_CURATION_FACTS: Final = 8_192
MAX_CURATION_BYTES: Final = 8 * 1024 * 1024
MAX_CURATION_CANDIDATES: Final = 4_096
_AUDIT_UUID_NAMESPACE: Final = UUID("5f2e5a6a-73ec-4c13-9c37-2c7f33c87c3d")
_AUDIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_AUDIT_HASH = re.compile(r"^[0-9a-f]{64}$")
_REASONING_REQUESTED = frozenset(("NONE", "LOW", "MEDIUM", "HIGH", "MAX"))
_REASONING_EFFECTIVE = frozenset(("UNKNOWN", "NONE", "LOW", "MEDIUM", "HIGH", "MAX"))
_AUDIT_OUTCOMES = frozenset(("SUCCESS", "FAILURE", "TIMEOUT", "ABSTAIN"))
CURATION_VALIDATOR_VERSION: Final = "p3f.validator.1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8", errors="strict")


def _audit_hash(value: object, field: str) -> str:
    if type(value) is not str or _AUDIT_HASH.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _audit_text(value: object, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or "\n" in value
        or "\r" in value
        or _AUDIT_REF.fullmatch(value) is None
    ):
        raise ValueError(f"{field} must use bounded canonical text")
    return value


class CurationAuditPort(Protocol):
    """Authoritative append-only sink for bounded curation metadata."""

    def append_curation_audit(self, record: CurationAuditRecord) -> bool: ...


@dataclass(frozen=True, slots=True)
class CurationAuditRecord:
    """Provider-attempt metadata; raw prompts, responses and secrets have no fields here."""

    audit_id: UUID
    artifact_id: str | None
    audit_kind: str
    route_id: str
    provider_id: str
    model_id: str
    policy_id: str
    template_hash: str
    reasoning_requested: str
    reasoning_effective: str
    attempt_count: int
    fallback_count: int
    input_hash: str
    output_hash: str | None
    report_hash: str | None
    case_count: int
    accepted_count: int
    latency_ms: int
    outcome: str

    def __post_init__(self) -> None:
        if type(self.audit_id) is not UUID:
            raise ValueError("audit_id must be an exact UUID")
        if self.artifact_id is not None:
            _audit_text(self.artifact_id, "artifact_id")
        if self.audit_kind not in {"MODEL", "EVAL"}:
            raise ValueError("audit_kind must be MODEL or EVAL")
        for name in ("route_id", "provider_id", "model_id", "policy_id"):
            _audit_text(getattr(self, name), name)
        _audit_hash(self.template_hash, "template_hash")
        if self.reasoning_requested not in _REASONING_REQUESTED:
            raise ValueError("reasoning_requested is outside its bound")
        if self.reasoning_effective not in _REASONING_EFFECTIVE:
            raise ValueError("reasoning_effective is outside its bound")
        if type(self.attempt_count) is not int or not 1 <= self.attempt_count <= 100:
            raise ValueError("attempt_count is outside its bound")
        if (
            type(self.fallback_count) is not int
            or not 0 <= self.fallback_count < self.attempt_count
        ):
            raise ValueError("fallback_count is outside its bound")
        _audit_hash(self.input_hash, "input_hash")
        if self.output_hash is not None:
            _audit_hash(self.output_hash, "output_hash")
        if self.report_hash is not None:
            _audit_hash(self.report_hash, "report_hash")
        report_required = self.audit_kind == "EVAL" or self.outcome in {"SUCCESS", "ABSTAIN"}
        if report_required != (self.report_hash is not None):
            raise ValueError("audit report hash does not match outcome policy")
        if type(self.case_count) is not int or not 0 <= self.case_count <= 100_000:
            raise ValueError("case_count is outside its bound")
        if (
            type(self.accepted_count) is not int
            or not 0 <= self.accepted_count <= self.case_count
        ):
            raise ValueError("accepted_count is outside its bound")
        if type(self.latency_ms) is not int or not 0 <= self.latency_ms <= 900_000:
            raise ValueError("latency_ms is outside its bound")
        if self.outcome not in _AUDIT_OUTCOMES:
            raise ValueError("outcome is outside its bound")
        if self.outcome == "SUCCESS" and self.output_hash is None:
            raise ValueError("successful audit requires an output hash")

    def db_parameters(self) -> tuple[object, ...]:
        """Return only the migration's typed metadata parameters, in exact DB order."""
        return (
            self.audit_id,
            self.artifact_id,
            self.audit_kind,
            self.route_id,
            self.provider_id,
            self.model_id,
            self.policy_id,
            self.template_hash,
            self.reasoning_requested,
            self.reasoning_effective,
            self.attempt_count,
            self.fallback_count,
            self.input_hash,
            self.output_hash,
            self.report_hash,
            self.case_count,
            self.accepted_count,
            self.latency_ms,
            self.outcome,
        )

    def verify_integrity(self) -> None:
        self.__post_init__()


class CurationAuditError(RuntimeError):
    """Raised when curation cannot establish its authoritative audit record."""


class InMemoryAppendOnlyCurationAuditRepository:
    """Exact, append-only audit sink for offline tests and deterministic replay."""

    def __init__(self) -> None:
        self._records: dict[UUID, CurationAuditRecord] = {}
        self._lock = threading.Lock()

    def append_curation_audit(self, record: CurationAuditRecord) -> bool:
        if type(record) is not CurationAuditRecord:
            raise ValueError("only an exact CurationAuditRecord can be appended")
        record.verify_integrity()
        with self._lock:
            existing = self._records.get(record.audit_id)
            if existing is not None:
                if existing == record:
                    return False
                raise RuntimeError("curation audit identity collision")
            self._records[record.audit_id] = record
            return True

    @property
    def records(self) -> tuple[CurationAuditRecord, ...]:
        with self._lock:
            return tuple(self._records.values())


@dataclass(frozen=True, slots=True, repr=False)
class CurationRequest:
    cutoff_at: UtcTimestamp
    source_records: tuple[DailyReflectionRecord, ...]
    template_id: str = CURATION_TEMPLATE_ID
    template_version: str = CURATION_TEMPLATE_VERSION
    template_hash: str = CURATION_TEMPLATE_HASH
    template: str = CURATION_TEMPLATE

    def __post_init__(self) -> None:
        if type(self.cutoff_at) is not UtcTimestamp:
            raise ValueError("curation cutoff must be an exact UtcTimestamp")
        if (
            type(self.source_records) is not tuple
            or not self.source_records
            or len(self.source_records) > MAX_ARTIFACT_SOURCES
            or any(type(item) is not DailyReflectionRecord for item in self.source_records)
        ):
            raise ValueError("curation source envelope is invalid")
        if (
            self.template_id != CURATION_TEMPLATE_ID
            or self.template_version != CURATION_TEMPLATE_VERSION
            or self.template_hash != CURATION_TEMPLATE_HASH
            or self.template != CURATION_TEMPLATE
            or hashlib.sha256(self.template.encode("utf-8")).hexdigest() != self.template_hash
        ):
            raise ValueError("curation template identity is not package-owned")
        facts = sum(
            len(source.facts) for record in self.source_records for source in record.sources
        )
        if facts > MAX_CURATION_FACTS:
            raise ValueError("curation source facts exceed aggregate bound")
        nodes = (
            sum(
                1 + len(record.sources) + len(record.observations) for record in self.source_records
            )
            + facts
        )
        if nodes > MAX_CURATION_NODES:
            raise ValueError("curation source nodes exceed aggregate bound")
        if any(
            item.available_at.value > self.cutoff_at.value
            or item.cutoff_at.value > self.cutoff_at.value
            for item in self.source_records
        ):
            raise ValueError("curation request includes future memory input")
        for item in self.source_records:
            item.verify_integrity()
        if len({item.record_id for item in self.source_records}) != len(self.source_records):
            raise ValueError("curation source record ids must be unique")
        fact_ids = tuple(
            fact.fact_id
            for record in self.source_records
            for source in record.sources
            for fact in source.facts
        )
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("curation source fact ids must be globally unique")
        material_bytes = 0
        for record in self.source_records:
            material_bytes += len(
                json.dumps(
                    record.content_wire(),
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8", errors="strict")
            )
            if material_bytes > MAX_CURATION_BYTES:
                raise ValueError("curation source bytes exceed aggregate bound")

    def __repr__(self) -> str:
        return "CurationRequest(<redacted>)"


def curation_input_hash(request: CurationRequest) -> str:
    """Hash the exact bounded input envelope without retaining its material in audit rows."""
    material = {
        "cutoff_at": str(request.cutoff_at),
        "template_hash": request.template_hash,
        "source_records": [record.content_wire() for record in request.source_records],
    }
    return hashlib.sha256(_canonical(material)).hexdigest()


class CurationProvider(Protocol):
    def curate(self, request: CurationRequest) -> tuple[MemoryCandidate, ...]: ...


class ScriptedCurationProvider:
    """Deterministic one-shot provider with no network, secrets, tools, shell or paths."""

    def __init__(self, candidates: tuple[MemoryCandidate, ...]) -> None:
        if type(candidates) is not tuple or not candidates:
            raise ValueError("scripted curation requires exact candidates")
        self._candidates = candidates
        self._used = False
        self.calls: list[CurationRequest] = []

    def curate(self, request: CurationRequest) -> tuple[MemoryCandidate, ...]:
        if self._used:
            raise RuntimeError("scripted curation output was already consumed")
        self._used = True
        self.calls.append(request)
        return self._candidates


def _candidate_material(candidate: MemoryCandidate) -> dict[str, object]:
    return {
        "entry": candidate.entry.to_wire(),
        "available_at": str(candidate.available_at),
        "recurrence_count": candidate.recurrence_count,
        "unresolved": candidate.unresolved,
        "model_importance": candidate.model_importance,
    }


def _validate_candidate_aggregate(candidates: tuple[MemoryCandidate, ...]) -> str:
    """Bound provider output before selection can discard the attacker's excess material."""
    total_nodes = 0
    total_bytes = 0
    material: list[dict[str, object]] = []
    for candidate in candidates:
        entry = candidate.entry
        total_nodes += (
            1
            + len(entry.applies_when)
            + len(entry.invalid_when)
            + len(entry.evidence_ids)
            + len(entry.source_record_ids)
            + len(entry.risk_reason_codes)
        )
        encoded = _canonical(_candidate_material(candidate))
        total_bytes += len(encoded)
        if total_nodes > MAX_CURATION_NODES or total_bytes > MAX_CURATION_BYTES:
            raise ValueError("curation candidate aggregate exceeds hard bound")
        material.append(_candidate_material(candidate))
    return hashlib.sha256(_canonical(material)).hexdigest()


@dataclass(frozen=True, slots=True)
class CurationPreparation:
    """Untrusted candidate plus bounded issues/audit, ready for one atomic PG commit."""

    artifact: MemoryArtifact
    issues: tuple[ValidationIssue, ...]
    audit: CurationAuditRecord
    input_hash: str
    case_count: int
    route_id: str
    provider_id: str
    model_id: str
    policy_id: str

    def __post_init__(self) -> None:
        if (
            type(self.artifact) is not MemoryArtifact
            or self.artifact.state is not ArtifactState.CANDIDATE
        ):
            raise ValueError("curation preparation must expose only a candidate artifact")
        if type(self.issues) is not tuple or any(
            type(item) is not ValidationIssue for item in self.issues
        ):
            raise ValueError("curation preparation issues are invalid")
        if type(self.audit) is not CurationAuditRecord:
            raise ValueError("curation preparation audit is invalid")
        _audit_hash(self.input_hash, "input_hash")
        if type(self.case_count) is not int or not 1 <= self.case_count <= MAX_CURATION_CANDIDATES:
            raise ValueError("curation preparation case count is invalid")
        for name in ("route_id", "provider_id", "model_id", "policy_id"):
            _audit_text(getattr(self, name), name)
        if (
            self.audit.input_hash != self.input_hash
            or self.audit.case_count != self.case_count
            or self.audit.route_id != self.route_id
            or self.audit.provider_id != self.provider_id
            or self.audit.model_id != self.model_id
            or self.audit.policy_id != self.policy_id
        ):
            raise ValueError("curation preparation/audit identity is inconsistent")
        self.artifact.verify_integrity()
        self.audit.verify_integrity()


class CurationPipeline:
    """Build and deterministically validate one candidate; promotion remains separate."""

    def __init__(
        self,
        provider: CurationProvider,
        validator: MemoryValidator,
        audit_port: CurationAuditPort,
        *,
        route_id: str = "p3f_compaction",
        provider_id: str = "offline.scripted",
        model_id: str = "deterministic",
        policy_id: str = "p3f.curation.v1",
        reasoning_requested: str = "NONE",
        reasoning_effective: str = "UNKNOWN",
        attempt_count: int = 1,
        fallback_count: int = 0,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if audit_port is None or not callable(getattr(audit_port, "append_curation_audit", None)):
            raise ValueError("curation requires an exact append-only audit capability")
        for name, value in (
            ("route_id", route_id),
            ("provider_id", provider_id),
            ("model_id", model_id),
            ("policy_id", policy_id),
        ):
            _audit_text(value, name)
        if reasoning_requested not in _REASONING_REQUESTED:
            raise ValueError("reasoning_requested is outside its bound")
        if reasoning_effective not in _REASONING_EFFECTIVE:
            raise ValueError("reasoning_effective is outside its bound")
        if type(attempt_count) is not int or not 1 <= attempt_count <= 100:
            raise ValueError("attempt_count is outside its bound")
        if type(fallback_count) is not int or not 0 <= fallback_count < attempt_count:
            raise ValueError("fallback_count is outside its bound")
        self._provider = provider
        self._validator = validator
        self._audit_port = audit_port
        self._route_id = route_id
        self._provider_id = provider_id
        self._model_id = model_id
        self._policy_id = policy_id
        self._reasoning_requested = reasoning_requested
        self._reasoning_effective = reasoning_effective
        self._attempt_count = attempt_count
        self._fallback_count = fallback_count
        self._clock = clock or (lambda: time.monotonic_ns() // 1_000_000)

    def _clock_ms(self) -> int:
        value = self._clock()
        if type(value) is not int or value < 0:
            raise CurationAuditError("curation clock returned an invalid value")
        return value

    def _input_hash(self, request: CurationRequest) -> str:
        return curation_input_hash(request)

    def _build_audit(
        self,
        *,
        input_hash: str,
        artifact_id: str | None,
        output_hash: str | None,
        case_count: int,
        accepted_count: int,
        outcome: str,
        started_ms: int,
        template_hash: str,
        execution_id: str,
        report_hash: str | None,
    ) -> CurationAuditRecord:
        elapsed = self._clock_ms() - started_ms
        if elapsed < 0 or elapsed > 900_000:
            raise CurationAuditError("curation audit latency is outside its bounded range")
        latency_ms = elapsed
        identity = {
            "domain": "seven-lens.p3f.curation-audit.v1",
            "route_id": self._route_id,
            "input_hash": input_hash,
            "artifact_id": artifact_id,
            "execution_id": execution_id,
            "attempt_ordinal": self._attempt_count,
        }
        audit_id = uuid5(_AUDIT_UUID_NAMESPACE, _canonical(identity).decode("utf-8"))
        record = CurationAuditRecord(
            audit_id,
            artifact_id,
            "MODEL",
            self._route_id,
            self._provider_id,
            self._model_id,
            self._policy_id,
            template_hash,
            self._reasoning_requested,
            self._reasoning_effective,
            self._attempt_count,
            self._fallback_count,
            input_hash,
            output_hash,
            report_hash,
            case_count,
            accepted_count,
            latency_ms,
            outcome,
        )
        return record

    def _append_audit(self, record: CurationAuditRecord) -> None:
        try:
            accepted = self._audit_port.append_curation_audit(record)
        except Exception as error:
            raise CurationAuditError("curation audit append failed") from error
        if type(accepted) is not bool:
            raise CurationAuditError("curation audit append returned an invalid result")

    @staticmethod
    def _report_hash(
        *,
        input_hash: str,
        output_hash: str | None,
        outcome: str,
        issues: tuple[tuple[str, str], ...],
        template_hash: str,
    ) -> str:
        return hashlib.sha256(
            b"seven-lens.p3f.curation-validation-report.v1\x00"
            + _canonical(
                {
                    "input_hash": input_hash,
                    "output_hash": output_hash,
                    "outcome": outcome,
                    "issues": list(issues),
                    "template_hash": template_hash,
                    "validator_version": CURATION_VALIDATOR_VERSION,
                }
            )
        ).hexdigest()

    def prepare(
        self,
        *,
        source_records: tuple[DailyReflectionRecord, ...],
        **artifact_fields: object,
    ) -> CurationPreparation:
        started_ms = self._clock()
        if type(started_ms) is not int or started_ms < 0:
            raise CurationAuditError("curation clock returned an invalid value")
        cutoff = artifact_fields.get("cutoff_at")
        if type(cutoff) is not UtcTimestamp:
            raise ValueError("curation artifact cutoff is invalid")
        execution_id = artifact_fields.pop("execution_id", None)
        if type(execution_id) is not str:
            raise ValueError("curation execution_id is required for replay identity")
        _audit_text(execution_id, "execution_id")
        request = CurationRequest(cutoff, source_records)
        input_hash = self._input_hash(request)
        candidates: tuple[MemoryCandidate, ...] = ()
        candidate_output_hash: str | None = None
        artifact_id: str | None = None
        try:
            candidates = self._provider.curate(request)
            if (
                type(candidates) is not tuple
                or not candidates
                or len(candidates) > MAX_CURATION_CANDIDATES
            ):
                raise ValueError("curation provider output must be a non-empty exact tuple")
            if any(type(item) is not MemoryCandidate for item in candidates):
                raise ValueError("curation provider output contains an invalid candidate")
            candidate_output_hash = _validate_candidate_aggregate(candidates)
            source_map = {item.record_id: item for item in source_records}
            if len(source_map) != len(source_records):
                raise ValueError("curation source record ids must be unique")
            artifact = build_selected_artifact(
                candidates, source_records=source_map, **artifact_fields
            )
            artifact_id = artifact.artifact_id
            result = self._validator.validate(
                artifact,
                source_records=source_map,
                requested_cutoff=cutoff,
            )
            output_hash = result.artifact.content_hash if result.artifact else candidate_output_hash
            outcome = "SUCCESS" if result.valid else "ABSTAIN"
            audit = self._build_audit(
                input_hash=input_hash,
                artifact_id=artifact_id,
                output_hash=output_hash,
                case_count=len(candidates),
                accepted_count=len(result.artifact.entries) if result.valid else 0,
                outcome=outcome,
                started_ms=started_ms,
                template_hash=request.template_hash,
                execution_id=execution_id,
                report_hash=self._report_hash(
                    input_hash=input_hash,
                    output_hash=output_hash,
                    outcome=outcome,
                    issues=tuple((item.stage, item.code) for item in result.issues),
                    template_hash=request.template_hash,
                ),
            )
            candidate_artifact = replace(result.artifact, state=ArtifactState.CANDIDATE)
            return CurationPreparation(
                candidate_artifact,
                result.issues,
                audit,
                input_hash,
                len(candidates),
                self._route_id,
                self._provider_id,
                self._model_id,
                self._policy_id,
            )
        except CurationAuditError:
            raise
        except TimeoutError:
            audit = self._build_audit(
                input_hash=input_hash,
                artifact_id=None,
                output_hash=candidate_output_hash,
                case_count=len(candidates),
                accepted_count=0,
                outcome="TIMEOUT",
                started_ms=started_ms,
                template_hash=request.template_hash,
                execution_id=execution_id,
                report_hash=None,
            )
            self._append_audit(audit)
            raise
        except Exception:
            audit = self._build_audit(
                input_hash=input_hash,
                artifact_id=None,
                output_hash=candidate_output_hash,
                case_count=len(candidates),
                accepted_count=0,
                outcome="FAILURE",
                started_ms=started_ms,
                template_hash=request.template_hash,
                execution_id=execution_id,
                report_hash=None,
            )
            self._append_audit(audit)
            raise

    def run(
        self,
        *,
        source_records: tuple[DailyReflectionRecord, ...],
        **artifact_fields: object,
    ) -> ValidationResult:
        """Run offline/in-memory curation and append its audit after preparation."""
        prepared = self.prepare(source_records=source_records, **artifact_fields)
        self._append_audit(prepared.audit)
        cutoff = artifact_fields.get("cutoff_at")
        if type(cutoff) is not UtcTimestamp:
            raise CurationAuditError("curation cutoff disappeared before final validation")
        source_map = {item.record_id: item for item in source_records}
        return self._validator.validate(
            prepared.artifact,
            source_records=source_map,
            requested_cutoff=cutoff,
        )
