"""Deterministic screening contracts: feature vectors and candidate sets.

``FeatureVector`` carries the nine raw subfactors plus the approved category
scores and composite for one security.  ``CandidateSet`` is the immutable
funnel output: quant top-100, evidence top-30, and focus 12/5.  Every record
is hash-bound, ordered deterministically, and never depends on dict/thread/DB
completion order.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from dataclasses import field as dataclass_field
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Final

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import SecurityId, SecuritySymbol, SourceRef
from seven_lens.sources.roles import P4SourceFamily

_HASH_DOMAIN: Final = b"seven-lens.p4c.feature-vector.v1\x00"
_CANDIDATE_HASH_DOMAIN: Final = b"seven-lens.p4c.candidate-set.v1\x00"
_SECTOR_ASSIGNMENT_DOMAIN: Final = b"seven-lens.p4c.sector-assignment.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
# CIK/SIC text is ASCII digits only: str.isdigit() would also accept Unicode
# decimal digits, and both authorities key on ASCII "0"-"9" alone.
_ASCII_DIGITS: Final = re.compile(r"^[0-9]+$")
_PRODUCER_VERSION: Final = "p4c.screening.v1"
_FORMULA_VERSION: Final = "p4-factor-v1.0"
_MAX_SCREENING_TEXT_BYTES: Final = 256
MAX_FEATURE_VECTOR_BYTES: Final = 1_048_576
MAX_CANDIDATE_SET_BYTES: Final = 4_194_304
MAX_SECTOR_ASSIGNMENT_BYTES: Final = 65_536
MAX_FEATURE_RAW_ITEMS: Final = 9
MAX_FEATURE_PRICE_SESSION_DATES: Final = 252
_EVIDENCE_SOURCE_FAMILIES: Final = frozenset(
    {
        P4SourceFamily.ALPACA_ASSETS,
        P4SourceFamily.ALPACA_CORPORATE_ACTIONS,
        P4SourceFamily.SEC_EDGAR,
        P4SourceFamily.ISSUER_IR,
        P4SourceFamily.EXCHANGE_OFFICIAL,
    }
)
_RAW_SUBFACTOR_NAMES: Final = (
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

QUANT_CAP: Final = 100
EVIDENCE_CAP: Final = 30
FOCUS_OPEN_CAP: Final = 12
FOCUS_CLOSE_CAP: Final = 5
MAX_CANDIDATE_SET_ENTRIES: Final = QUANT_CAP + EVIDENCE_CAP + FOCUS_OPEN_CAP + FOCUS_CLOSE_CAP


def _bounded_text(value: object, label: str, *, allow_empty: bool = False) -> None:
    """Enforce the byte bound used by screening contract text fields."""
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError(f"{label} requires bounded text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{label} requires valid UTF-8 text") from exc
    if b"\x00" in encoded or len(encoded) > _MAX_SCREENING_TEXT_BYTES:
        raise ValueError(f"{label} exceeds the {_MAX_SCREENING_TEXT_BYTES}-byte text bound")


def _canonical_json_bytes(wire: dict[str, object], *, limit: int, label: str) -> bytes:
    """Serialize canonical UTF-8 JSON and enforce the persisted wire cap."""
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(canonical) > limit:
        raise ValueError(f"{label} exceeds the {limit}-byte serialized cap")
    return canonical


@dataclass(frozen=True, slots=True)
class _HashAuthority:
    """Private content-bound capability used by trusted finalizers/readback."""

    content_hash: str


@dataclass(frozen=True, slots=True)
class _CandidateEntryAuthority:
    """Private capability bound to every immutable CandidateEntry field."""

    stage: CandidateStage
    fingerprint: tuple[object, ...]


class FactorStatus(StrEnum):
    """Closed outcome of one security's factor evaluation."""

    COMPLETE = "COMPLETE"
    FACTOR_INPUT_MISSING = "FACTOR_INPUT_MISSING"
    FACTOR_MANIFEST_NOT_APPROVED = "FACTOR_MANIFEST_NOT_APPROVED"
    SECTOR_TAXONOMY_NOT_AUTHORIZED = "SECTOR_TAXONOMY_NOT_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class RawFeature:
    """One raw subfactor value plus its provenance."""

    name: str
    value: Decimal | None
    formula_version: str
    source_refs: tuple[SourceRef, ...]
    security_id: SecurityId | None = None
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        _bounded_text(self.name, "raw feature name")
        if self.value is not None and (
            type(self.value) is not Decimal or not self.value.is_finite()
        ):
            raise ValueError("raw feature value must be a finite Decimal or None")
        _bounded_text(self.formula_version, "raw feature formula_version")
        if (
            type(self.source_refs) is not tuple
            or not self.source_refs
            or len(self.source_refs) > 64
            or any(type(ref) is not SourceRef for ref in self.source_refs)
        ):
            raise ValueError("raw feature source_refs require 1 to 64 exact SourceRef values")
        if len({ref.record_id for ref in self.source_refs}) != len(self.source_refs):
            raise ValueError("raw feature source_refs must not repeat a record")
        if self.source_refs != tuple(
            sorted(
                self.source_refs, key=lambda ref: (ref.family.value, ref.record_id, ref.record_hash)
            )
        ):
            raise ValueError("raw feature source_refs must use canonical order")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("raw feature security_id requires an exact SecurityId or None")
        if self.missing_reason is not None:
            _bounded_text(self.missing_reason, "missing_reason")
        if self.value is None and (self.missing_reason is None or not self.missing_reason):
            raise ValueError("missing raw features require a non-empty missing reason")
        if self.value is not None and self.missing_reason is not None:
            raise ValueError("present raw features cannot carry a missing reason")


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """One security's complete factor evaluation.

    All nine raw subfactors are mandatory; a missing raw subfactor makes the
    security's factor status ``FACTOR_INPUT_MISSING`` and it never enters the
    quant set.  Negative earnings/CFO/FCF are legal low values and are never
    turned into missing or zero.
    """

    security_id: SecurityId
    symbol: SecuritySymbol
    universe_hash: str
    manifest_hash: str
    as_of: UtcTimestamp
    known_at: UtcTimestamp
    status: FactorStatus
    raw: tuple[RawFeature, ...]
    trend: Decimal | None
    quality: Decimal | None
    value: Decimal | None
    low_risk: Decimal | None
    composite: Decimal | None
    missing_reason: str | None
    schema_version: SchemaVersion
    feature_hash: str
    price_session_dates: tuple[TradingDate, ...] = ()
    _authority: _HashAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _HashAuthority:
            raise ValueError("feature vectors must be finalized by the screening authority")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.universe_hash) is not str or _HASH_TEXT.fullmatch(self.universe_hash) is None:
            raise ValueError("universe_hash must be a SHA-256 digest")
        if type(self.manifest_hash) is not str or _HASH_TEXT.fullmatch(self.manifest_hash) is None:
            raise ValueError("manifest_hash must be a SHA-256 digest")
        if type(self.as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        if type(self.known_at) is not UtcTimestamp:
            raise ValueError("known_at requires canonical UTC")
        if self.known_at.value > self.as_of.value:
            raise ValueError("known_at cannot be after as_of")
        if type(self.status) is not FactorStatus:
            raise ValueError("status requires an exact FactorStatus")
        if self.status is FactorStatus.COMPLETE:
            from seven_lens.screening.manifests import factor_manifest

            if self.manifest_hash != factor_manifest().manifest_hash:
                raise ValueError("COMPLETE vectors require the approved factor manifest")
        if (
            type(self.raw) is not tuple
            or len(self.raw) != MAX_FEATURE_RAW_ITEMS
            or any(type(r) is not RawFeature for r in self.raw)
        ):
            raise ValueError("raw must be a tuple of RawFeature values")
        if any(
            type(raw.security_id) is not SecurityId or raw.security_id != self.security_id
            for raw in self.raw
        ):
            raise ValueError("raw feature lineage must bind to the feature security")
        raw_names = [r.name for r in self.raw]
        if tuple(raw_names) != _RAW_SUBFACTOR_NAMES:
            raise ValueError("raw features must exactly match the approved factor manifest order")
        if any(raw.formula_version != _FORMULA_VERSION for raw in self.raw):
            raise ValueError("raw features must use the approved factor formula version")
        for name in ("trend", "quality", "value", "low_risk", "composite"):
            value = getattr(self, name)
            if value is not None and (type(value) is not Decimal or not value.is_finite()):
                raise ValueError(f"{name} must be a finite Decimal or None")
        if self.status is FactorStatus.COMPLETE:
            if any(r.value is None for r in self.raw):
                raise ValueError("COMPLETE status requires every raw feature value")
            if self.missing_reason is not None:
                raise ValueError("COMPLETE status cannot carry a missing reason")
            if any(
                getattr(self, name) is None
                for name in ("trend", "quality", "value", "low_risk", "composite")
            ):
                raise ValueError("COMPLETE status requires every category score")
        elif self.missing_reason is None:
            raise ValueError("non-COMPLETE status requires a missing reason")
        elif any(
            getattr(self, name) is not None
            for name in ("trend", "quality", "value", "low_risk", "composite")
        ):
            raise ValueError("non-COMPLETE status cannot carry category scores")
        if self.missing_reason is not None:
            _bounded_text(self.missing_reason, "missing_reason")
        if type(self.schema_version) is not SchemaVersion:
            raise ValueError("schema_version requires an exact SchemaVersion")
        if type(self.feature_hash) is not str or _HASH_TEXT.fullmatch(self.feature_hash) is None:
            raise ValueError("feature_hash must be a SHA-256 digest")
        if type(self.price_session_dates) is not tuple or any(
            type(trading_date) is not TradingDate for trading_date in self.price_session_dates
        ):
            raise ValueError("price_session_dates require exact TradingDate values")
        price_dates = [trading_date.value for trading_date in self.price_session_dates]
        if price_dates != sorted(price_dates) or len(set(price_dates)) != len(price_dates):
            raise ValueError("price_session_dates must be ordered and unique")
        if (
            self.status is FactorStatus.COMPLETE
            and len(self.price_session_dates) != MAX_FEATURE_PRICE_SESSION_DATES
        ):
            raise ValueError("COMPLETE vectors require the latest 252 price sessions")
        if self.status is not FactorStatus.COMPLETE and self.price_session_dates:
            raise ValueError("non-COMPLETE vectors cannot carry price session dates")
        if self.feature_hash != self.compute_hash():
            raise ValueError("feature_hash does not match frozen content")
        if self._authority.content_hash != self.feature_hash:
            raise ValueError("feature-vector authority is not bound to frozen content")

    def wire(self) -> dict[str, object]:
        def _raw_wire(raw: RawFeature) -> dict[str, object]:
            if raw.security_id is None:
                raise ValueError("raw feature lineage must bind to the feature security")
            return {
                "name": raw.name,
                "value": None if raw.value is None else str(raw.value),
                "formula_version": raw.formula_version,
                "security_id": raw.security_id.value,
                "source_refs": [
                    {
                        "record_id": ref.record_id,
                        "family": ref.family.value,
                        "record_hash": ref.record_hash,
                    }
                    for ref in raw.source_refs
                ],
                "missing_reason": raw.missing_reason,
            }

        return {
            "security_id": self.security_id.value,
            "symbol": self.symbol.value,
            "universe_hash": self.universe_hash,
            "manifest_hash": self.manifest_hash,
            "as_of": str(self.as_of),
            "known_at": str(self.known_at),
            "status": self.status.value,
            "raw": [_raw_wire(r) for r in self.raw],
            "trend": None if self.trend is None else str(self.trend),
            "quality": None if self.quality is None else str(self.quality),
            "value": None if self.value is None else str(self.value),
            "low_risk": None if self.low_risk is None else str(self.low_risk),
            "composite": None if self.composite is None else str(self.composite),
            "missing_reason": self.missing_reason,
            "schema_version": str(self.schema_version),
            "producer_version": _PRODUCER_VERSION,
            "price_session_dates": [str(trading_date) for trading_date in self.price_session_dates],
        }

    def compute_hash(self) -> str:
        canonical = _canonical_json_bytes(
            self.wire(), limit=MAX_FEATURE_VECTOR_BYTES, label="feature vector"
        )
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.feature_hash != self.compute_hash():
            raise ValueError("feature_hash does not match frozen content")
        return True


def build_feature_vector(**values: object) -> FeatureVector:
    """Reject untrusted public finalization of a feature vector.

    Feature vectors are authoritative outputs of the screening assembler (or
    validated DB readback).  Keeping this compatibility name fail-closed
    prevents a caller from presenting self-authored raw values with a valid
    hash and source references as production screening input.
    """
    raise ValueError("feature vectors must be finalized by the screening authority")


def _feature_vector_body(values: Mapping[str, object]) -> dict[str, object]:
    """Normalize trusted-finalizer input without accepting a caller hash."""
    from dataclasses import MISSING, fields

    body = {name: value for name, value in values.items() if name != "feature_hash"}
    security_id = body.get("security_id")
    raw = body.get("raw")
    if type(security_id) is SecurityId and isinstance(raw, (list, tuple)):
        body["raw"] = tuple(
            replace(item, security_id=security_id)
            if type(item) is RawFeature and item.security_id is None
            else item
            for item in raw
        )
    for field in fields(FeatureVector):
        if field.name not in body and field.name != "feature_hash" and field.default is not MISSING:
            body[field.name] = field.default
    return body


def _finalize_feature_vector(**values: object) -> FeatureVector:
    """Finalize a feature vector for the trusted screening assembler."""
    body = _feature_vector_body(values)
    computed = _derive_hash(**body)
    body["_authority"] = _HashAuthority(computed)
    return FeatureVector(**body, feature_hash=computed)  # type: ignore[arg-type]


def _reconstruct_feature_vector(**values: object) -> FeatureVector:
    """Reconstruct a feature vector after DB wire/hash validation."""
    feature_hash = values.get("feature_hash")
    if type(feature_hash) is not str or _HASH_TEXT.fullmatch(feature_hash) is None:
        raise ValueError("feature_hash must be a SHA-256 digest")
    body = _feature_vector_body(values)
    body["_authority"] = _HashAuthority(feature_hash)
    return FeatureVector(**body, feature_hash=feature_hash)  # type: ignore[arg-type]


def _derive_hash(**body: object) -> str:
    """Compute the domain-separated hash for a provisional FeatureVector."""
    provisional = object.__new__(FeatureVector)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "feature_hash", "")
    return provisional.compute_hash()


class CandidateStage(StrEnum):
    """The funnel stage that produced one candidate."""

    QUANT = "QUANT"
    EVIDENCE = "EVIDENCE"
    FOCUS_OPEN = "FOCUS_OPEN"
    FOCUS_CLOSE = "FOCUS_CLOSE"


@dataclass(frozen=True, slots=True)
class CandidateEntry:
    """One ordered funnel candidate with its approved final score lineage."""

    security_id: SecurityId
    symbol: SecuritySymbol
    composite: Decimal
    trend: Decimal
    quality: Decimal
    value: Decimal
    low_risk: Decimal
    stage: CandidateStage
    feature_hash: str
    universe_hash: str
    quarantine_decision_hash: str
    sector_assignment_hash: str | None = None
    evidence_source_refs: tuple[SourceRef, ...] = ()
    reasons: tuple[ClosedReason, ...] = ()
    _authority: _CandidateEntryAuthority | None = dataclass_field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self._authority) is not _CandidateEntryAuthority:
            raise ValueError("candidate entries must be finalized by the screening stage authority")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        for name in ("composite", "trend", "quality", "value", "low_risk"):
            value = getattr(self, name)
            if type(value) is not Decimal or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if type(self.stage) is not CandidateStage:
            raise ValueError("stage requires an exact CandidateStage")
        for name in ("feature_hash", "universe_hash", "quarantine_decision_hash"):
            value = getattr(self, name)
            if type(value) is not str or _HASH_TEXT.fullmatch(value) is None:
                raise ValueError(f"{name} must be a SHA-256 digest")
        if self.sector_assignment_hash is not None and (
            type(self.sector_assignment_hash) is not str
            or _HASH_TEXT.fullmatch(self.sector_assignment_hash) is None
        ):
            raise ValueError("sector_assignment_hash must be a SHA-256 digest or None")
        if self.stage is CandidateStage.QUANT and self.sector_assignment_hash is not None:
            raise ValueError("quant candidates cannot carry a sector-assignment reference")
        if self.stage is not CandidateStage.QUANT and self.sector_assignment_hash is None:
            raise ValueError("non-quant candidates require a sector-assignment reference")
        if type(self.evidence_source_refs) is not tuple or any(
            type(ref) is not SourceRef for ref in self.evidence_source_refs
        ):
            raise ValueError("evidence_source_refs require exact SourceRef values")
        if len(self.evidence_source_refs) > 64:
            raise ValueError("evidence_source_refs require at most 64 SourceRef values")
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
        if self.stage is CandidateStage.QUANT and self.evidence_source_refs:
            raise ValueError("quant candidates cannot carry evidence source references")
        if self.stage is not CandidateStage.QUANT and not self.evidence_source_refs:
            raise ValueError("non-quant candidates require typed evidence source references")
        if type(self.reasons) is not tuple or any(
            type(r) is not ClosedReason for r in self.reasons
        ):
            raise ValueError("reasons require exact ClosedReason values")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must be unique")
        reason_order = {reason: index for index, reason in enumerate(ClosedReason)}
        if self.reasons != tuple(sorted(self.reasons, key=reason_order.__getitem__)):
            raise ValueError("reasons must use the canonical order")
        if self._authority.fingerprint != _candidate_entry_fingerprint(self):
            raise ValueError("candidate-entry authority is not bound to frozen content")
        if self._authority.stage is not self.stage:
            raise ValueError("candidate-entry authority is not bound to its funnel stage")


def _candidate_entry_fingerprint(entry: CandidateEntry) -> tuple[object, ...]:
    """Return the immutable content covered by a stage authority token."""
    return (
        entry.security_id,
        entry.symbol,
        entry.composite,
        entry.trend,
        entry.quality,
        entry.value,
        entry.low_risk,
        entry.stage,
        entry.feature_hash,
        entry.universe_hash,
        entry.quarantine_decision_hash,
        entry.sector_assignment_hash,
        entry.evidence_source_refs,
        entry.reasons,
    )


def _candidate_entry_body(values: Mapping[str, object]) -> dict[str, object]:
    """Normalize trusted stage-finalizer input and fill only safe defaults."""
    from dataclasses import MISSING

    body = dict(values)
    for candidate_field in fields(CandidateEntry):
        if candidate_field.name not in body and candidate_field.default is not MISSING:
            body[candidate_field.name] = candidate_field.default
    body.pop("_authority", None)
    return body


def _finalize_candidate_entry(**values: object) -> CandidateEntry:
    """Finalize one entry from a trusted quant/evidence/focus stage."""
    body = _candidate_entry_body(values)
    # Build a small provisional object only to obtain the content fingerprint;
    # CandidateEntry itself still enforces every field invariant.
    provisional = object.__new__(CandidateEntry)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    stage = body.get("stage")
    if type(stage) is not CandidateStage:
        raise ValueError("candidate-entry stage is required before authority finalization")
    body["_authority"] = _CandidateEntryAuthority(stage, _candidate_entry_fingerprint(provisional))
    return CandidateEntry(**body)  # type: ignore[arg-type]


def _reconstruct_candidate_entry(**values: object) -> CandidateEntry:
    """Reconstruct one entry after DB wire/hash validation."""
    body = _candidate_entry_body(values)
    provisional = object.__new__(CandidateEntry)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    stage = body.get("stage")
    if type(stage) is not CandidateStage:
        raise ValueError("candidate-entry stage is required before authority reconstruction")
    body["_authority"] = _CandidateEntryAuthority(stage, _candidate_entry_fingerprint(provisional))
    return CandidateEntry(**body)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class CandidateSet:
    """The immutable funnel output: quant → evidence → focus 12/5.

    ``quant`` has at most 100 entries, ``evidence`` at most 30 (a subset of
    quant in the same order), ``focus_open`` at most 12, and ``focus_close``
    at most 5 (both subsets of evidence).  Order follows the approved
    tie-break: composite, trend, quality, value, low_risk descending, then
    stable security id ascending.  No entry is added to fill a cap.
    """

    as_of: UtcTimestamp
    known_at: UtcTimestamp
    factor_manifest_hash: str
    cluster_manifest_hash: str
    universe_hash: str
    quant: tuple[CandidateEntry, ...]
    evidence: tuple[CandidateEntry, ...]
    focus_open: tuple[CandidateEntry, ...]
    focus_close: tuple[CandidateEntry, ...]
    policy_hash: str
    producer_version: str
    schema_version: SchemaVersion
    candidate_hash: str
    _authority: object | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _HashAuthority:
            raise ValueError("candidate sets must be built by the authority constructor")
        if type(self.as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        if type(self.known_at) is not UtcTimestamp:
            raise ValueError("known_at requires canonical UTC")
        if self.known_at.value > self.as_of.value:
            raise ValueError("known_at cannot be after as_of")
        for name in (
            "factor_manifest_hash",
            "cluster_manifest_hash",
            "universe_hash",
            "policy_hash",
        ):
            value = getattr(self, name)
            if type(value) is not str or _HASH_TEXT.fullmatch(value) is None:
                raise ValueError(f"{name} must be a SHA-256 digest")
        from seven_lens.screening.manifests import cluster_manifest, factor_manifest

        if self.factor_manifest_hash != factor_manifest().manifest_hash:
            raise ValueError("candidate set requires the approved factor manifest")
        if self.cluster_manifest_hash != cluster_manifest().manifest_hash:
            raise ValueError("candidate set requires the approved cluster manifest")
        if type(self.schema_version) is not SchemaVersion:
            raise ValueError("schema_version requires an exact SchemaVersion")
        for name, cap in (
            ("quant", QUANT_CAP),
            ("evidence", EVIDENCE_CAP),
            ("focus_open", FOCUS_OPEN_CAP),
            ("focus_close", FOCUS_CLOSE_CAP),
        ):
            values = getattr(self, name)
            if (
                type(values) is not tuple
                or len(values) > cap
                or any(type(e) is not CandidateEntry for e in values)
            ):
                raise ValueError(f"{name} must be a tuple of at most {cap} CandidateEntry values")
        if (
            sum(
                len(getattr(self, name))
                for name in ("quant", "evidence", "focus_open", "focus_close")
            )
            > MAX_CANDIDATE_SET_ENTRIES
        ):
            raise ValueError("candidate set exceeds the maximum stage item count")
        for stage_name in ("quant", "evidence", "focus_open", "focus_close"):
            for entry in getattr(self, stage_name):
                if entry.universe_hash != self.universe_hash:
                    raise ValueError("candidate entry is bound to a different universe")
        stage_expectations = (
            ("quant", CandidateStage.QUANT),
            ("evidence", CandidateStage.EVIDENCE),
            ("focus_open", CandidateStage.FOCUS_OPEN),
            ("focus_close", CandidateStage.FOCUS_CLOSE),
        )
        for name, expected_stage in stage_expectations:
            stage_values = getattr(self, name)
            if any(entry.stage is not expected_stage for entry in stage_values):
                raise ValueError(f"{name} contains an entry from the wrong stage")
            stage_ids = [entry.security_id.value for entry in stage_values]
            if len(stage_ids) != len(set(stage_ids)):
                raise ValueError(f"{name} must not repeat a security")
            ordered = tuple(
                sorted(
                    stage_values,
                    key=lambda entry: (
                        -entry.composite,
                        -entry.trend,
                        -entry.quality,
                        -entry.value,
                        -entry.low_risk,
                        entry.security_id.value,
                    ),
                )
            )
            if stage_values != ordered:
                raise ValueError(f"{name} must use the canonical score order")
        for name, expected in (
            ("evidence", self.quant),
            ("focus_open", self.evidence),
            ("focus_close", self.evidence),
        ):
            current = getattr(self, name)
            ids = [e.security_id.value for e in current]
            parent_ids = {e.security_id.value for e in expected}
            if any(security_id not in parent_ids for security_id in ids):
                raise ValueError(f"{name} must be a subset of its parent stage")
            parent_by_id = {entry.security_id.value: entry for entry in expected}
            for entry in current:
                parent = parent_by_id[entry.security_id.value]
                if (
                    entry.symbol != parent.symbol
                    or entry.composite != parent.composite
                    or entry.trend != parent.trend
                    or entry.quality != parent.quality
                    or entry.value != parent.value
                    or entry.low_risk != parent.low_risk
                    or entry.feature_hash != parent.feature_hash
                    or entry.universe_hash != parent.universe_hash
                    or entry.quarantine_decision_hash != parent.quarantine_decision_hash
                    or (
                        name in ("focus_open", "focus_close")
                        and entry.evidence_source_refs != parent.evidence_source_refs
                    )
                    or (
                        name in ("focus_open", "focus_close")
                        and entry.sector_assignment_hash != parent.sector_assignment_hash
                    )
                ):
                    raise ValueError(f"{name} must preserve parent identity and score lineage")
            parent_order = {entry.security_id.value: index for index, entry in enumerate(expected)}
            if ids != sorted(ids, key=parent_order.__getitem__):
                raise ValueError(f"{name} must preserve parent stage order")
        _bounded_text(self.producer_version, "producer_version")
        if self.producer_version != _PRODUCER_VERSION:
            raise ValueError("candidate set producer_version is not approved")
        if (
            type(self.candidate_hash) is not str
            or _HASH_TEXT.fullmatch(self.candidate_hash) is None
        ):
            raise ValueError("candidate_hash must be a SHA-256 digest")
        if self.candidate_hash != self.compute_hash():
            raise ValueError("candidate_hash does not match frozen content")
        if self._authority.content_hash != self.candidate_hash:
            raise ValueError("candidate-set authority is not bound to frozen content")

    def wire(self) -> dict[str, object]:
        def _entry(e: CandidateEntry) -> dict[str, object]:
            return {
                "security_id": e.security_id.value,
                "symbol": e.symbol.value,
                "composite": str(e.composite),
                "trend": str(e.trend),
                "quality": str(e.quality),
                "value": str(e.value),
                "low_risk": str(e.low_risk),
                "stage": e.stage.value,
                "feature_hash": e.feature_hash,
                "universe_hash": e.universe_hash,
                "quarantine_decision_hash": e.quarantine_decision_hash,
                "sector_assignment_hash": e.sector_assignment_hash,
                "evidence_source_refs": [
                    {
                        "record_id": ref.record_id,
                        "family": ref.family.value,
                        "record_hash": ref.record_hash,
                    }
                    for ref in e.evidence_source_refs
                ],
                "reasons": [r.value for r in e.reasons],
            }

        return {
            "as_of": str(self.as_of),
            "known_at": str(self.known_at),
            "factor_manifest_hash": self.factor_manifest_hash,
            "cluster_manifest_hash": self.cluster_manifest_hash,
            "universe_hash": self.universe_hash,
            "quant": [_entry(e) for e in self.quant],
            "evidence": [_entry(e) for e in self.evidence],
            "focus_open": [_entry(e) for e in self.focus_open],
            "focus_close": [_entry(e) for e in self.focus_close],
            "policy_hash": self.policy_hash,
            "producer_version": self.producer_version,
            "schema_version": str(self.schema_version),
        }

    def compute_hash(self) -> str:
        canonical = _canonical_json_bytes(
            self.wire(), limit=MAX_CANDIDATE_SET_BYTES, label="candidate set"
        )
        return sha256(_CANDIDATE_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.candidate_hash != self.compute_hash():
            raise ValueError("candidate_hash does not match frozen content")
        return True


def build_candidate_set(**values: object) -> CandidateSet:
    """Build a candidate set while deriving, never trusting, its hash.

    The feature-vector mapping is deliberately not serialized.  It is an
    authority input used to prove that every candidate score is copied from
    the complete parent vector before the candidate wire is frozen.
    """
    from dataclasses import MISSING

    feature_vectors = values.pop("feature_vectors", None)
    if isinstance(feature_vectors, Mapping):
        vector_values = tuple(feature_vectors.values())
    elif type(feature_vectors) is tuple or isinstance(feature_vectors, list):
        vector_values = tuple(feature_vectors)
    else:
        raise ValueError("feature_vectors are required to bind candidate scores to parents")
    if any(type(vector) is not FeatureVector for vector in vector_values):
        raise ValueError("feature_vectors require exact FeatureVector values")
    vector_by_id = {vector.security_id.value: vector for vector in vector_values}
    if len(vector_by_id) != len(vector_values):
        raise ValueError("feature_vectors must not repeat a security")

    body = {name: value for name, value in values.items() if name != "candidate_hash"}
    for candidate_field in fields(CandidateSet):
        if (
            candidate_field.name not in body
            and candidate_field.name != "candidate_hash"
            and candidate_field.default is not MISSING
        ):
            body[candidate_field.name] = candidate_field.default
    if not isinstance(body.get("as_of"), UtcTimestamp) or not isinstance(
        body.get("known_at"), UtcTimestamp
    ):
        raise ValueError("candidate timestamps are required before score binding")
    candidate_as_of = body["as_of"]
    candidate_known_at = body["known_at"]
    if type(candidate_as_of) is not UtcTimestamp or type(candidate_known_at) is not UtcTimestamp:
        raise ValueError("candidate timestamps are required before score binding")
    stage_entries: list[object] = []
    for stage_name in ("quant", "evidence", "focus_open", "focus_close"):
        stage_values = body.get(stage_name, ())
        if type(stage_values) is not tuple and not isinstance(stage_values, list):
            raise ValueError("candidate stages require a tuple or list")
        stage_entries.extend(stage_values)
    entries = tuple(stage_entries)
    for entry in entries:
        if type(entry) is not CandidateEntry:
            raise ValueError("candidate stages require exact CandidateEntry values")
        vector = vector_by_id.get(entry.security_id.value)
        if (
            vector is None
            or vector.status is not FactorStatus.COMPLETE
            or vector.symbol != entry.symbol
            or vector.feature_hash != entry.feature_hash
            or vector.universe_hash != entry.universe_hash
            or vector.as_of != candidate_as_of
            or vector.known_at.value > candidate_known_at.value
            or vector.composite != entry.composite
            or vector.trend != entry.trend
            or vector.quality != entry.quality
            or vector.value != entry.value
            or vector.low_risk != entry.low_risk
        ):
            raise ValueError("candidate score is not copied from its complete feature vector")
    candidate_hash = _derive_candidate_hash(**body)
    body["_authority"] = _HashAuthority(candidate_hash)
    return CandidateSet(**body, candidate_hash=candidate_hash)  # type: ignore[arg-type]


def _reconstruct_candidate_set(**values: object) -> CandidateSet:
    """Reconstruct a DB-validated candidate set without reopening its input map."""
    from dataclasses import MISSING

    body = {name: value for name, value in values.items() if name != "candidate_hash"}
    for candidate_field in fields(CandidateSet):
        if (
            candidate_field.name not in body
            and candidate_field.name != "candidate_hash"
            and candidate_field.default is not MISSING
        ):
            body[candidate_field.name] = candidate_field.default
    candidate_hash = values["candidate_hash"]
    if type(candidate_hash) is not str or _HASH_TEXT.fullmatch(candidate_hash) is None:
        raise ValueError("candidate_hash must be a SHA-256 digest")
    body["_authority"] = _HashAuthority(candidate_hash)
    return CandidateSet(**body, candidate_hash=candidate_hash)  # type: ignore[arg-type]


def _derive_candidate_hash(**body: object) -> str:
    """Compute the domain-separated hash for a provisional CandidateSet."""
    provisional = object.__new__(CandidateSet)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "candidate_hash", "")
    return provisional.compute_hash()


@dataclass(frozen=True, slots=True)
class SectorAssignment:
    """One security's point-in-time SEC SIC Division assignment.

    Carries the exact CIK, the zero-padded SIC string, the closed division
    label, the source record reference/accession, the availability instant,
    and the taxonomy manifest version/hash so the assignment is fully
    auditable.  ``SECTOR_UNKNOWN`` assignments never enter new-exposure
    candidates.
    """

    security_id: SecurityId
    cik: str
    sic: str
    division: str
    source_ref: SourceRef
    accession: str | None
    available_at: UtcTimestamp
    taxonomy_version: str
    taxonomy_hash: str
    assignment_hash: str
    _authority: _HashAuthority | None = dataclass_field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._authority) is not _HashAuthority:
            raise ValueError("sector assignments must be finalized by the screening authority")
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if (
            type(self.cik) is not str
            or len(self.cik) != 10
            or _ASCII_DIGITS.fullmatch(self.cik) is None
        ):
            raise ValueError("cik must be ten digits")
        if (
            type(self.sic) is not str
            or len(self.sic) not in (3, 4)
            or _ASCII_DIGITS.fullmatch(self.sic) is None
        ):
            raise ValueError("sic must be a 3- or 4-digit string")
        if len(self.sic) == 3:
            object.__setattr__(self, "sic", self.sic.zfill(4))
        _bounded_text(self.division, "division")
        if type(self.source_ref) is not SourceRef:
            raise ValueError("source_ref requires an exact SourceRef")
        if self.source_ref.family is not P4SourceFamily.SEC_EDGAR:
            raise ValueError("sector assignments require the SEC EDGAR authority")
        if self.accession is not None:
            _bounded_text(self.accession, "accession")
        if type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC")
        _bounded_text(self.taxonomy_version, "taxonomy_version")
        if self.taxonomy_version != "sec-sic-division-v1":
            raise ValueError("taxonomy_version must be sec-sic-division-v1")
        from seven_lens.screening.manifests import SicDivision, classify_sic, sector_manifest

        if self.division not in {division.value for division in SicDivision}:
            raise ValueError("division must be an approved SEC SIC division")
        if self.division != classify_sic(self.sic).value:
            raise ValueError("division does not match the approved SIC classification")
        if self.taxonomy_hash != sector_manifest().manifest_hash:
            raise ValueError("taxonomy_hash must identify the approved sector manifest")
        if type(self.taxonomy_hash) is not str or (
            _HASH_TEXT.fullmatch(self.taxonomy_hash) is None
        ):
            raise ValueError("taxonomy_hash must be a SHA-256 digest")
        if (
            type(self.assignment_hash) is not str
            or _HASH_TEXT.fullmatch(self.assignment_hash) is None
        ):
            raise ValueError("assignment_hash must be a SHA-256 digest")
        if self.assignment_hash != self.compute_hash():
            raise ValueError("assignment_hash does not match frozen content")
        if self._authority.content_hash != self.assignment_hash:
            raise ValueError("sector-assignment authority is not bound to frozen content")

    def wire(self) -> dict[str, object]:
        return {
            "security_id": self.security_id.value,
            "cik": self.cik,
            "sic": self.sic,
            "division": self.division,
            "source_ref": {
                "record_id": self.source_ref.record_id,
                "family": self.source_ref.family.value,
                "record_hash": self.source_ref.record_hash,
            },
            "accession": self.accession,
            "available_at": str(self.available_at),
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_hash": self.taxonomy_hash,
        }

    def compute_hash(self) -> str:
        canonical = _canonical_json_bytes(
            self.wire(), limit=MAX_SECTOR_ASSIGNMENT_BYTES, label="sector assignment"
        )
        return sha256(_SECTOR_ASSIGNMENT_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.assignment_hash != self.compute_hash():
            raise ValueError("assignment_hash does not match frozen content")
        return True


def build_sector_assignment(**values: object) -> SectorAssignment:
    """Reject untrusted public finalization of a sector assignment."""
    raise ValueError("sector assignments must be finalized by the screening authority")


def _sector_assignment_body(values: Mapping[str, object]) -> dict[str, object]:
    """Normalize trusted sector-finalizer input without accepting a hash."""
    sic = values.get("sic")
    if type(sic) is str and len(sic) == 3 and _ASCII_DIGITS.fullmatch(sic) is not None:
        values = {**values, "sic": sic.zfill(4)}
    body = {name: value for name, value in values.items() if name != "assignment_hash"}
    body.pop("_authority", None)
    return body


def _finalize_sector_assignment(**values: object) -> SectorAssignment:
    """Finalize an assignment from the trusted SEC normalization path."""
    body = _sector_assignment_body(values)
    provisional = object.__new__(SectorAssignment)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "assignment_hash", "")
    computed = provisional.compute_hash()
    body["_authority"] = _HashAuthority(computed)
    return SectorAssignment(**body, assignment_hash=computed)  # type: ignore[arg-type]


def _reconstruct_sector_assignment(**values: object) -> SectorAssignment:
    """Reconstruct an assignment after DB wire/hash validation."""
    assignment_hash = values.get("assignment_hash")
    if type(assignment_hash) is not str or _HASH_TEXT.fullmatch(assignment_hash) is None:
        raise ValueError("assignment_hash must be a SHA-256 digest")
    body = _sector_assignment_body(values)
    body["_authority"] = _HashAuthority(assignment_hash)
    return SectorAssignment(**body, assignment_hash=assignment_hash)  # type: ignore[arg-type]
