"""Normalized P4-A source records and strict provider-wire parsing helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from typing import Final, cast

from seven_lens.domain.json_values import JsonObject
from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import (
    CoverageLabel,
    P4SourceFamily,
    SourceFamilyPolicy,
    SourceManifestRegistry,
    SourceRole,
    p4_manifest_registry,
)

_HASH_DOMAIN: Final = b"seven-lens.p4.source-record.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_RECORD_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_ENDPOINT_ID: Final = re.compile(r"^[a-z0-9_]{1,64}$")
_DATE_TEXT: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_MAX_PAYLOAD_BYTES: Final = 4_000_000
_MAX_JSON_DEPTH: Final = 16
_MAX_JSON_NODES: Final = 20_000
_ADAPTER_RECORD_AUTHORITY: Final = object()
_RECORD_READBACK_AUTHORITY: Final = object()

_CANONICAL_STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"


class SourceSchemaDriftError(ValueError):
    """Raised when provider payload shape violates the family schema."""


class ProviderTimestampError(ValueError):
    """Raised when provider timestamp text is not a bounded canonical variant."""


def strict_json_loads(payload: bytes) -> object:
    """Decode provider JSON rejecting duplicates, non-finite numbers, and drift."""
    if type(payload) is not bytes or not payload or len(payload) > _MAX_PAYLOAD_BYTES:
        raise SourceSchemaDriftError("provider payload is missing or oversized")

    def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise SourceSchemaDriftError("provider payload contains duplicate keys")
            result[key] = value
        return result

    def _reject_constant(raw: str) -> None:
        raise SourceSchemaDriftError(f"provider payload contains non-finite constant {raw}")

    try:
        decoded = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise SourceSchemaDriftError("provider payload is not valid JSON") from error
    if not isinstance(decoded, (dict, list)):
        raise SourceSchemaDriftError("provider payload must decode to an object or array")
    _check_bounds(decoded, depth=0)
    return decoded


def _check_bounds(value: object, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise SourceSchemaDriftError("provider payload exceeds depth bound")
    if isinstance(value, dict):
        if len(value) > 256:
            raise SourceSchemaDriftError("provider payload object exceeds member bound")
        for nested in cast(dict[str, object], value).values():
            _check_bounds(nested, depth + 1)
        return
    if isinstance(value, list):
        if len(value) > 10_000:
            raise SourceSchemaDriftError("provider payload array exceeds item bound")
        for nested in cast(list[object], value):
            _check_bounds(nested, depth + 1)


def parse_provider_timestamp(text: str) -> UtcTimestamp:
    """Parse provider timestamps bounded to canonical UTC variants; never guess."""
    if type(text) is not str:
        raise ProviderTimestampError("provider timestamp must be text")
    for pattern, fmt in (
        (r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{1,9}Z$", None),
        (r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", "%Y-%m-%dT%H:%M:%SZ"),
        (r"^\d{8}T\d{6}Z$", "%Y%m%dT%H%M%SZ"),
        (r"^\d{14}$", "%Y%m%d%H%M%S"),
    ):
        if re.fullmatch(pattern, text) is None:
            continue
        try:
            if fmt is None:
                head, fraction = text.rstrip("Z").split(".")
                fraction = (fraction + "000000")[:6]
                parsed = datetime.strptime(f"{head}.{fraction}Z", _CANONICAL_STAMP)
            else:
                parsed = datetime.strptime(text, fmt)
        except ValueError as error:
            raise ProviderTimestampError("provider timestamp is not a valid instant") from error
        return UtcTimestamp(parsed.replace(tzinfo=UTC))
    raise ProviderTimestampError("provider timestamp uses an unsupported format")


def provider_utc_date(text: str) -> UtcTimestamp:
    """Convert a provider date-only value into midnight-UTC point-in-time."""
    match = _DATE_TEXT.fullmatch(text) if type(text) is str else None
    if match is None:
        raise ProviderTimestampError("provider date must use YYYY-MM-DD")
    try:
        parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=UTC)
    except ValueError as error:
        raise ProviderTimestampError("provider date is not a valid calendar date") from error
    return UtcTimestamp(parsed)


@dataclass(frozen=True, slots=True)
class NormalizedSourceRecord:
    """One typed, point-in-time, hash-bound record produced by a family adapter."""

    record_id: str
    family: P4SourceFamily
    endpoint_id: str
    schema_version: SchemaVersion
    content_hash: str
    record_hash: str
    retrieved_at: UtcTimestamp
    payload: JsonObject
    material_claim: bool
    observation_at: UtcTimestamp | None = None
    published_at: UtcTimestamp | None = None
    available_at: UtcTimestamp | None = None
    effective_at: UtcTimestamp | None = None
    vintage: tuple[str, str] | None = None
    supersedes_content_hash: str | None = None
    coverage_warning: str | None = None
    _authority: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._authority is not _ADAPTER_RECORD_AUTHORITY
            and self._authority is not _RECORD_READBACK_AUTHORITY
        ):
            raise ValueError(
                "normalized records must be produced by an adapter or trusted readback"
            )
        if type(self.record_id) is not str or _RECORD_ID.fullmatch(self.record_id) is None:
            raise ValueError("record_id is not a canonical record identifier")
        if type(self.endpoint_id) is not str or _ENDPOINT_ID.fullmatch(self.endpoint_id) is None:
            raise ValueError("endpoint_id is not a sanitized identifier")
        if not any(endpoint.endpoint_id == self.endpoint_id for endpoint in self._policy.endpoints):
            raise ValueError("endpoint_id is not registered for the source family")
        if type(self.schema_version) is not SchemaVersion:
            raise ValueError("schema_version requires an exact SchemaVersion")
        if type(self.content_hash) is not str or _HASH_TEXT.fullmatch(self.content_hash) is None:
            raise ValueError("content_hash must be a SHA-256 digest")
        if type(self.retrieved_at) is not UtcTimestamp:
            raise ValueError("retrieved_at requires canonical UTC")
        for name in ("observation_at", "published_at", "available_at", "effective_at"):
            value = getattr(self, name)
            if value is not None and type(value) is not UtcTimestamp:
                raise ValueError(f"{name} requires canonical UTC or None")
            if value is not None and value.value > self.retrieved_at.value:
                raise ValueError(f"{name} cannot be after retrieval")
        if self.vintage is not None:
            if type(self.vintage) is not tuple or len(self.vintage) != 2:
                raise ValueError("vintage requires a start/end date pair")
            provider_utc_date(self.vintage[0])
            provider_utc_date(self.vintage[1])
            if self.vintage[0] > self.vintage[1]:
                raise ValueError("vintage window is inverted")
        if self.supersedes_content_hash is not None and (
            type(self.supersedes_content_hash) is not str
            or _HASH_TEXT.fullmatch(self.supersedes_content_hash) is None
            or self.supersedes_content_hash == self.content_hash
        ):
            raise ValueError("superseded content hash is invalid")
        if type(self.payload) is not JsonObject:
            raise ValueError("payload requires canonical JsonObject")
        if type(self.material_claim) is not bool:
            raise ValueError("material_claim requires an exact bool")
        if self.role in (SourceRole.DISCOVERY, SourceRole.RESEARCH_SUPPLEMENT) and (
            self.material_claim
        ):
            raise ValueError("discovery and supplement records can never be material")
        if self.family is P4SourceFamily.ALPACA_IEX_QUOTES:
            if self.coverage is not CoverageLabel.LIMITED_MARKET_COVERAGE:
                raise ValueError("IEX records must carry limited market coverage")
            if type(self.coverage_warning) is not str or not self.coverage_warning:
                raise ValueError("IEX records require the mandatory coverage warning")
        elif self.coverage_warning is not None:
            raise ValueError("coverage warning applies only to limited-coverage records")
        if not self.role or not self.coverage or not self.rights:
            raise ValueError("record policy dimensions must resolve from the registry")
        if type(self.record_hash) is not str or _HASH_TEXT.fullmatch(self.record_hash) is None:
            raise ValueError("record_hash must be a SHA-256 digest")
        if self.record_hash != self.compute_hash():
            raise ValueError("record_hash does not match frozen record content")

    def _validated_family(self) -> P4SourceFamily:
        if type(self.family) is not P4SourceFamily:
            raise ValueError("family requires an exact P4SourceFamily")
        return self.family

    @property
    def _policy(self) -> SourceFamilyPolicy:
        return _registry().policy(self.family)

    @property
    def role(self) -> SourceRole:
        return self._policy.role

    @property
    def coverage(self) -> CoverageLabel:
        return self._policy.coverage

    @property
    def rights(self) -> RightsStatus:
        return self._policy.rights

    @property
    def producer_version(self) -> str:
        return "p4a.adapters.v1"

    def wire(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "family": self.family.value,
            "endpoint_id": self.endpoint_id,
            "schema_version": str(self.schema_version),
            "content_hash": self.content_hash,
            "retrieved_at": str(self.retrieved_at),
            "role": self.role.value,
            "coverage": self.coverage.value,
            "rights": self.rights.value,
            "producer_version": self.producer_version,
            "payload": (
                json.loads(self.payload.to_json())
                if isinstance(self.payload, JsonObject)
                else self.payload
            ),
            "material_claim": self.material_claim,
            "observation_at": None if self.observation_at is None else str(self.observation_at),
            "published_at": None if self.published_at is None else str(self.published_at),
            "available_at": None if self.available_at is None else str(self.available_at),
            "effective_at": None if self.effective_at is None else str(self.effective_at),
            "vintage": None if self.vintage is None else list(self.vintage),
            "supersedes_content_hash": self.supersedes_content_hash,
            "coverage_warning": self.coverage_warning,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.record_hash != self.compute_hash():
            raise ValueError("record_hash does not match frozen record content")
        return True


def _build_normalized_record(**values: object) -> NormalizedSourceRecord:
    """Build a record while deriving, never trusting, its content-bound hash."""
    values = {**values, "_authority": _ADAPTER_RECORD_AUTHORITY}
    if isinstance(values.get("payload"), dict):
        values = {**values, "payload": canonical_payload(values["payload"])}
    provisional = object.__new__(NormalizedSourceRecord)
    for field_item in fields(NormalizedSourceRecord):
        if field_item.name in values:
            object.__setattr__(provisional, field_item.name, values[field_item.name])
        else:
            object.__setattr__(provisional, field_item.name, field_item.default)
    object.__setattr__(provisional, "record_hash", provisional.compute_hash())
    return NormalizedSourceRecord(**values, record_hash=provisional.compute_hash())  # type: ignore[arg-type]


def _reconstruct_normalized_record(
    *, authority: object, record_hash: str, **values: object
) -> NormalizedSourceRecord:
    """Rebuild a stored record behind the persistence readback capability."""
    if authority is not _RECORD_READBACK_AUTHORITY:
        raise ValueError("normalized record reconstruction requires trusted readback authority")
    record = _build_normalized_record(**values)
    object.__setattr__(record, "_authority", _RECORD_READBACK_AUTHORITY)
    if record.record_hash != record_hash:
        raise ValueError("stored normalized record hash does not match its canonical content")
    return record


def build_normalized_record(**values: object) -> NormalizedSourceRecord:
    """Reject caller-authored source authority; adapters own record construction."""
    del values
    raise ValueError("normalized record construction is an adapter-only API")


def content_hash_of(payload: bytes) -> str:
    """Return the SHA-256 digest of exact provider payload bytes."""
    return sha256(payload).hexdigest()


def producer_version() -> str:
    return "p4a.adapters.v1"


def schema_version(value: str) -> SchemaVersion:
    return SchemaVersion(value)


def require_type(value: object, expected: type, field: str) -> object:
    if type(value) is not expected:
        raise SourceSchemaDriftError(f"{field} has an unexpected type")
    return value


def require_keys(data: dict[str, object], required: set[str], allowed: set[str]) -> None:
    missing = required - set(data)
    unknown = set(data) - allowed
    if missing:
        raise SourceSchemaDriftError(f"provider payload is missing keys: {sorted(missing)}")
    if unknown:
        raise SourceSchemaDriftError(f"provider payload carries unknown keys: {sorted(unknown)}")


def require_timestamp(value: object, field: str) -> UtcTimestamp:
    if type(value) is not str:
        raise SourceSchemaDriftError(f"{field} must be timestamp text")
    try:
        return parse_provider_timestamp(value)
    except ProviderTimestampError as error:
        raise SourceSchemaDriftError(f"{field} is not a bounded provider timestamp") from error


def require_date(value: object, field: str) -> UtcTimestamp:
    if type(value) is not str:
        raise SourceSchemaDriftError(f"{field} must be date text")
    try:
        return provider_utc_date(value)
    except ProviderTimestampError as error:
        raise SourceSchemaDriftError(f"{field} is not a bounded provider date") from error


def require_decimal_text(value: object, field: str) -> str:
    if type(value) is not str or not re.fullmatch(r"\d{1,12}(\.\d{1,8})?", value):
        raise SourceSchemaDriftError(f"{field} must be a non-negative decimal text")
    return value


def canonical_payload(value: object) -> JsonObject:
    try:
        return JsonObject.from_value(value)
    except ValueError as error:
        raise SourceSchemaDriftError("normalized payload is not canonical JSON") from error


@lru_cache(maxsize=1)
def _registry() -> SourceManifestRegistry:
    return p4_manifest_registry()
