"""Strict point-in-time source and frozen evidence contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import RunId, SchemaVersion, UtcTimestamp

SOURCE_SCHEMA_VERSION: Final = SchemaVersion("1.0.0")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class SourceFamily(StrEnum):
    SEC = "SEC"
    ISSUER = "ISSUER"
    EXCHANGE = "EXCHANGE"
    MARKET_VENDOR = "MARKET_VENDOR"
    NEWS_PUBLISHER = "NEWS_PUBLISHER"
    PUBLIC_WEB = "PUBLIC_WEB"
    SEARCH = "SEARCH"


class SourceKind(StrEnum):
    FILING = "FILING"
    ISSUER_RELEASE = "ISSUER_RELEASE"
    EXCHANGE_NOTICE = "EXCHANGE_NOTICE"
    ARTICLE = "ARTICLE"
    MARKET_DATA = "MARKET_DATA"
    SEARCH_RESULT = "SEARCH_RESULT"


class AccessMethod(StrEnum):
    HTTPS_GET = "HTTPS_GET"
    LICENSED_FIXTURE = "LICENSED_FIXTURE"


class RightsStatus(StrEnum):
    ALLOWED = "ALLOWED"
    METADATA_ONLY = "METADATA_ONLY"
    UNKNOWN = "UNKNOWN"
    PROHIBITED = "PROHIBITED"


class RobotsStatus(StrEnum):
    ALLOWED = "ALLOWED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"
    PROHIBITED = "PROHIBITED"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNVERIFIED = "UNVERIFIED"


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    DATA_CONFLICT = "DATA_CONFLICT"
    TOMBSTONED = "TOMBSTONED"


def _bounded(value: str, field: str, maximum: int = 2048) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{field} must be non-empty bounded text")
    if len(value.encode("utf-8", errors="strict")) > maximum:
        raise ValueError(f"{field} exceeds its byte bound")
    return value


def _ref(value: str, field: str) -> str:
    if type(value) is not str or _REF.fullmatch(value) is None:
        raise ValueError(f"{field} is not a canonical reference")
    return value


def _hash(value: str, field: str) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(f"{field} is not a SHA-256 digest")
    return value


def _canonical_url(value: str) -> str:
    _bounded(value, "canonical_url", 2048)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("canonical_url must be credential-free canonical HTTPS") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.hostname != parsed.hostname.lower()
    ):
        raise ValueError("canonical_url must be credential-free canonical HTTPS")
    canonical = urlunsplit(("https", parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    if canonical != value:
        raise ValueError("canonical_url is not canonical")
    return value


def _unique(
    values: tuple[str, ...], field: str, pattern: re.Pattern[str] = _REF
) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) > 64:
        raise ValueError(f"{field} must be a bounded tuple")
    if any(type(item) is not str or pattern.fullmatch(item) is None for item in values):
        raise ValueError(f"{field} contains a non-canonical value")
    if len(values) != len(set(values)):
        raise ValueError(f"{field} contains duplicates")
    return values


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    canonical_url: str
    publisher: str
    source_family: SourceFamily
    source_kind: SourceKind
    access_method: AccessMethod
    published_at: UtcTimestamp | None
    discovered_at: UtcTimestamp
    retrieved_at: UtcTimestamp
    available_at: UtcTimestamp | None
    content_hash: str
    content_type: str
    language: str
    rights_status: RightsStatus
    robots_status: RobotsStatus
    primary_source: bool
    ticker_tags: tuple[str, ...]
    claim_tags: tuple[str, ...]
    supersedes: str | None = None
    tombstone: bool = False

    def __post_init__(self) -> None:
        _ref(self.source_id, "source_id")
        _canonical_url(self.canonical_url)
        _bounded(self.publisher, "publisher", 256)
        for name, kind in (
            ("source_family", SourceFamily),
            ("source_kind", SourceKind),
            ("access_method", AccessMethod),
            ("rights_status", RightsStatus),
            ("robots_status", RobotsStatus),
        ):
            if type(getattr(self, name)) is not kind:
                raise ValueError(f"{name} requires an exact enum")
        if (
            type(self.discovered_at) is not UtcTimestamp
            or type(self.retrieved_at) is not UtcTimestamp
        ):
            raise ValueError("source timestamps require canonical UTC")
        if self.published_at is not None and type(self.published_at) is not UtcTimestamp:
            raise ValueError("published_at requires canonical UTC or null")
        if self.available_at is not None and type(self.available_at) is not UtcTimestamp:
            raise ValueError("available_at requires canonical UTC or null")
        if self.discovered_at.value > self.retrieved_at.value:
            raise ValueError("source timestamps are out of order")
        if self.available_at is not None and self.available_at.value > self.retrieved_at.value:
            raise ValueError("available_at cannot be after retrieval")
        _hash(self.content_hash, "content_hash")
        _bounded(self.content_type, "content_type", 128)
        _bounded(self.language, "language", 16)
        if type(self.primary_source) is not bool or type(self.tombstone) is not bool:
            raise ValueError("source flags require exact bool")
        _unique(self.ticker_tags, "ticker_tags", _SYMBOL)
        _unique(self.claim_tags, "claim_tags")
        if self.supersedes is not None:
            _ref(self.supersedes, "supersedes")
            if self.supersedes == self.source_id:
                raise ValueError("source cannot supersede itself")

    def eligible_at(self, as_of: UtcTimestamp) -> bool:
        return (
            type(as_of) is UtcTimestamp
            and self.available_at is not None
            and self.available_at.value <= as_of.value
            and self.retrieved_at.value <= as_of.value
            and (self.published_at is None or self.published_at.value <= as_of.value)
            and not self.tombstone
            and self.rights_status not in {RightsStatus.UNKNOWN, RightsStatus.PROHIBITED}
            and self.robots_status not in {RobotsStatus.UNKNOWN, RobotsStatus.PROHIBITED}
        )


@dataclass(frozen=True, slots=True)
class SourceFragment:
    fragment_id: str
    source_id: str
    content_hash: str
    excerpt: str
    available_at: UtcTimestamp
    verified: bool
    prompt_injection_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _ref(self.fragment_id, "fragment_id")
        _ref(self.source_id, "source_id")
        _hash(self.content_hash, "content_hash")
        _bounded(self.excerpt, "excerpt", 4096)
        if type(self.available_at) is not UtcTimestamp or type(self.verified) is not bool:
            raise ValueError("fragment time/verification type is invalid")
        _unique(self.prompt_injection_flags, "prompt_injection_flags")


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_id: str
    statement: str
    fragment_refs: tuple[str, ...]
    material: bool

    def __post_init__(self) -> None:
        _ref(self.claim_id, "claim_id")
        _bounded(self.statement, "statement", 2048)
        _unique(self.fragment_refs, "fragment_refs")
        if type(self.material) is not bool:
            raise ValueError("material requires exact bool")
        if self.material and not self.fragment_refs:
            raise ValueError("material claim requires evidence")


@dataclass(frozen=True, slots=True)
class EvidencePacket:
    schema_version: SchemaVersion
    packet_id: RunId
    as_of: UtcTimestamp
    source_records: tuple[SourceRecord, ...]
    fragments: tuple[SourceFragment, ...]
    claims: tuple[EvidenceClaim, ...]
    contradiction_claim_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    freshness_status: FreshnessStatus
    status: EvidenceStatus
    universe_hash: str
    portfolio_snapshot_hash: str
    data_snapshot_refs: tuple[str, ...]
    producer_version: str
    packet_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != SOURCE_SCHEMA_VERSION or type(self.packet_id) is not RunId:
            raise ValueError("unsupported evidence identity")
        if type(self.as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        for name, values, kind, maximum in (
            ("source_records", self.source_records, SourceRecord, 128),
            ("fragments", self.fragments, SourceFragment, 256),
            ("claims", self.claims, EvidenceClaim, 128),
        ):
            if (
                type(values) is not tuple
                or len(values) > maximum
                or any(type(x) is not kind for x in values)
            ):
                raise ValueError(f"{name} is not a bounded exact tuple")
        source_ids = {item.source_id for item in self.source_records}
        fragment_ids = {item.fragment_id for item in self.fragments}
        claim_ids = {item.claim_id for item in self.claims}
        if (
            len(source_ids) != len(self.source_records)
            or len(fragment_ids) != len(self.fragments)
            or len(claim_ids) != len(self.claims)
        ):
            raise ValueError("packet identities must be unique")
        if any(item.source_id not in source_ids for item in self.fragments):
            raise ValueError("fragment references a source outside the packet")
        by_source = {item.source_id: item for item in self.source_records}
        for fragment in self.fragments:
            source = by_source[fragment.source_id]
            if not source.eligible_at(self.as_of) or fragment.available_at.value > self.as_of.value:
                raise ValueError("packet contains point-in-time ineligible evidence")
            source_available = source.available_at
            if source_available is None or fragment.available_at.value < source_available.value:
                raise ValueError("packet fragment predates its source availability")
            if not fragment.verified or fragment.content_hash != source.content_hash:
                raise ValueError("packet contains unverified content identity")
        if any(ref not in fragment_ids for claim in self.claims for ref in claim.fragment_refs):
            raise ValueError("claim citation is dangling or cross-packet")
        _unique(self.contradiction_claim_ids, "contradiction_claim_ids")
        if not set(self.contradiction_claim_ids) <= claim_ids:
            raise ValueError("contradiction references an unknown claim")
        _unique(self.missing_evidence, "missing_evidence")
        if (
            type(self.freshness_status) is not FreshnessStatus
            or type(self.status) is not EvidenceStatus
        ):
            raise ValueError("packet status requires exact enums")
        if self.status is EvidenceStatus.VERIFIED and (
            self.freshness_status is not FreshnessStatus.FRESH
            or self.contradiction_claim_ids
            or self.missing_evidence
        ):
            raise ValueError("verified packet must be fresh, complete, and contradiction-free")
        _hash(self.universe_hash, "universe_hash")
        _hash(self.portfolio_snapshot_hash, "portfolio_snapshot_hash")
        _unique(self.data_snapshot_refs, "data_snapshot_refs")
        _bounded(self.producer_version, "producer_version", 64)
        _hash(self.packet_hash, "packet_hash")
        if self.packet_hash != self.compute_hash():
            raise ValueError("packet_hash does not match frozen content")

    @property
    def citation_ids(self) -> frozenset[str]:
        return frozenset(fragment.fragment_id for fragment in self.fragments)

    def validate_integrity(self) -> None:
        """Re-run nested and packet invariants at an authority boundary."""
        for source in self.source_records:
            source.__post_init__()
        for fragment in self.fragments:
            fragment.__post_init__()
        for claim in self.claims:
            claim.__post_init__()
        self.__post_init__()

    def compute_hash(self) -> str:
        payload = {
            "schema_version": str(self.schema_version),
            "packet_id": str(self.packet_id),
            "as_of": str(self.as_of),
            "sources": [
                {
                    "source_id": x.source_id,
                    "canonical_url": x.canonical_url,
                    "publisher": x.publisher,
                    "source_family": x.source_family.value,
                    "source_kind": x.source_kind.value,
                    "access_method": x.access_method.value,
                    "published_at": None if x.published_at is None else str(x.published_at),
                    "discovered_at": str(x.discovered_at),
                    "retrieved_at": str(x.retrieved_at),
                    "available_at": None if x.available_at is None else str(x.available_at),
                    "content_hash": x.content_hash,
                    "content_type": x.content_type,
                    "language": x.language,
                    "rights_status": x.rights_status.value,
                    "robots_status": x.robots_status.value,
                    "primary_source": x.primary_source,
                    "ticker_tags": list(x.ticker_tags),
                    "claim_tags": list(x.claim_tags),
                    "supersedes": x.supersedes,
                    "tombstone": x.tombstone,
                }
                for x in self.source_records
            ],
            "fragments": [
                {
                    "fragment_id": x.fragment_id,
                    "source_id": x.source_id,
                    "content_hash": x.content_hash,
                    "excerpt": x.excerpt,
                    "available_at": str(x.available_at),
                    "verified": x.verified,
                    "prompt_injection_flags": list(x.prompt_injection_flags),
                }
                for x in self.fragments
            ],
            "claims": [
                {
                    "claim_id": x.claim_id,
                    "statement": x.statement,
                    "fragment_refs": list(x.fragment_refs),
                    "material": x.material,
                }
                for x in self.claims
            ],
            "contradictions": list(self.contradiction_claim_ids),
            "missing": list(self.missing_evidence),
            "freshness": self.freshness_status.value,
            "status": self.status.value,
            "universe_hash": self.universe_hash,
            "portfolio_snapshot_hash": self.portfolio_snapshot_hash,
            "data_snapshot_refs": list(self.data_snapshot_refs),
            "producer_version": self.producer_version,
        }
        return hashlib.sha256(JsonObject.from_value(payload).to_json().encode()).hexdigest()


def build_evidence_packet(**values: object) -> EvidencePacket:
    """Build a packet while deriving, never trusting, its content hash."""
    provisional = object.__new__(EvidencePacket)
    for name, value in {**values, "packet_hash": "0" * 64}.items():
        object.__setattr__(provisional, name, value)
    return EvidencePacket(**values, packet_hash=provisional.compute_hash())  # type: ignore[arg-type]
