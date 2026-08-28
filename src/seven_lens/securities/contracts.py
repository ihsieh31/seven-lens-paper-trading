"""Point-in-time security identity contracts for the P4-B security master.

A symbol is never an identity: one ticker may be reused by different securities
over time, and one stable security may change its ticker.  ``valid_from`` and
``valid_to`` describe real-world validity; ``available_at`` describes when the
system could first know a record.  The two axes are never interchangeable, and
a record learned late is invisible at any historical cutoff before its
available time.  Withdrawals and corrections are append-only supersessions;
nothing here ever rewrites history.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from enum import StrEnum
from hashlib import sha256
from typing import Final

from seven_lens.domain.value_objects import SchemaVersion, UtcTimestamp
from seven_lens.sources.roles import P4SourceFamily

_HASH_DOMAIN: Final = b"seven-lens.p4b.security-identity.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_SECURITY_ID: Final = re.compile(r"^[0-9a-f][0-9a-f-]{7,63}$")
_SYMBOL: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_CIK: Final = re.compile(r"^[0-9]{10}$")
_CUSIP: Final = re.compile(r"^[0-9A-Z]{9}$")
_ISIN: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_RECORD_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_PRODUCER_VERSION: Final = "p4b.securities.v1"

MAX_SOURCE_REFS: Final = 16


class AssetClass(StrEnum):
    """Closed asset classes; only the P4-A pinned class is representable."""

    US_EQUITY = "us_equity"


class ListingExchange(StrEnum):
    """Closed listing exchanges known to the security master."""

    AMEX = "AMEX"
    ARCA = "ARCA"
    BATS = "BATS"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"


class SecurityStatus(StrEnum):
    """Provider-reported status; never inferred, never guessed."""

    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class SecurityId:
    """The stable identity of one security; survives symbol changes."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _SECURITY_ID.fullmatch(self.value) is None:
            raise ValueError("security id must be canonical lowercase provider identity text")


@dataclass(frozen=True, slots=True)
class SecuritySymbol:
    """A ticker in canonical uppercase broker form; never an identity by itself."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _SYMBOL.fullmatch(self.value) is None:
            raise ValueError("symbol must be uppercase letters, digits, '.' or '-' (max 10)")


@dataclass(frozen=True, slots=True)
class Cik:
    """A ten-digit zero-padded SEC Central Index Key."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CIK.fullmatch(self.value) is None:
            raise ValueError("cik must be exactly ten digits")


@dataclass(frozen=True, slots=True)
class Cusip:
    """A nine-character CUSIP identifier."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _CUSIP.fullmatch(self.value) is None:
            raise ValueError("cusip must be exactly nine uppercase alphanumeric characters")


@dataclass(frozen=True, slots=True)
class Isin:
    """A twelve-character ISIN with country prefix and check digit."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _ISIN.fullmatch(self.value) is None:
            raise ValueError("isin must be two uppercase letters, nine alphanumerics, one digit")


@dataclass(frozen=True, slots=True)
class ValidityInterval:
    """A half-open real-world validity window ``[valid_from, valid_to)``."""

    valid_from: UtcTimestamp
    valid_to: UtcTimestamp | None

    def __post_init__(self) -> None:
        if type(self.valid_from) is not UtcTimestamp:
            raise ValueError("validity interval start requires canonical UTC")
        if self.valid_to is not None and type(self.valid_to) is not UtcTimestamp:
            raise ValueError("validity interval end requires canonical UTC or None")
        if self.valid_to is not None and self.valid_to.value <= self.valid_from.value:
            raise ValueError("validity interval must start strictly before it ends")

    def contains(self, point: UtcTimestamp) -> bool:
        """Return whether one instant lies inside the half-open window."""
        if type(point) is not UtcTimestamp:
            raise ValueError("interval containment requires canonical UTC")
        if point.value < self.valid_from.value:
            return False
        return self.valid_to is None or point.value < self.valid_to.value


def intervals_overlap(first: ValidityInterval, second: ValidityInterval) -> bool:
    """Return whether two half-open validity windows share any instant."""
    if type(first) is not ValidityInterval or type(second) is not ValidityInterval:
        raise ValueError("interval overlap requires exact ValidityInterval values")
    if first.valid_to is not None and second.valid_from.value >= first.valid_to.value:
        return False
    return second.valid_to is None or first.valid_from.value < second.valid_to.value


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One hash-bound P4-A source record reference; the lineage anchor."""

    record_id: str
    family: P4SourceFamily
    record_hash: str

    def __post_init__(self) -> None:
        if type(self.record_id) is not str or _RECORD_ID.fullmatch(self.record_id) is None:
            raise ValueError("record id must be a canonical record identifier")
        if type(self.family) is not P4SourceFamily:
            raise ValueError("family requires an exact P4SourceFamily")
        if type(self.record_hash) is not str or _HASH_TEXT.fullmatch(self.record_hash) is None:
            raise ValueError("record hash must be a SHA-256 digest")


def _validate_identity_fields(values: Mapping[str, object]) -> None:
    """Fail closed on any field that violates the identity contract."""
    if type(values.get("security_id")) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    if type(values.get("symbol")) is not SecuritySymbol:
        raise ValueError("symbol requires an exact SecuritySymbol")
    if type(values.get("exchange")) is not ListingExchange:
        raise ValueError("exchange requires an exact ListingExchange")
    if type(values.get("asset_class")) is not AssetClass:
        raise ValueError("asset_class requires an exact AssetClass")
    for name, expected in (("cik", Cik), ("cusip", Cusip), ("isin", Isin)):
        value = values.get(name)
        if value is not None and type(value) is not expected:
            raise ValueError(f"{name} requires an exact {expected.__name__} or None")
    valid_from = values.get("valid_from")
    if type(valid_from) is not UtcTimestamp:
        raise ValueError("valid_from requires canonical UTC")
    valid_to = values.get("valid_to")
    if valid_to is not None and type(valid_to) is not UtcTimestamp:
        raise ValueError("valid_to requires canonical UTC or None")
    if valid_to is not None and valid_to.value <= valid_from.value:
        raise ValueError("validity interval must start strictly before it ends")
    if type(values.get("available_at")) is not UtcTimestamp:
        raise ValueError("available_at requires canonical UTC")
    if type(values.get("status")) is not SecurityStatus:
        raise ValueError("status requires an exact SecurityStatus")
    source_refs = values.get("source_refs")
    if type(source_refs) is not tuple or not source_refs or len(source_refs) > MAX_SOURCE_REFS:
        raise ValueError(
            f"source_refs must be a non-empty tuple of at most {MAX_SOURCE_REFS} SourceRef values"
        )
    if any(type(ref) is not SourceRef for ref in source_refs):
        raise ValueError("source_refs require exact SourceRef values")
    record_ids = [ref.record_id for ref in source_refs]
    if len(set(record_ids)) != len(record_ids):
        raise ValueError("source_refs must carry unique record identifiers")
    if type(values.get("schema_version")) is not SchemaVersion:
        raise ValueError("schema_version requires an exact SchemaVersion")


@dataclass(frozen=True, slots=True)
class SecurityIdentityRecord:
    """One immutable, point-in-time identity observation for a stable security.

    ``valid_from``/``valid_to`` bound real-world validity; ``available_at``
    bounds system knowledge.  The record is content-hashed; any post-construction
    tamper breaks ``verify_integrity``.
    """

    security_id: SecurityId
    symbol: SecuritySymbol
    exchange: ListingExchange
    asset_class: AssetClass
    valid_from: UtcTimestamp
    available_at: UtcTimestamp
    status: SecurityStatus
    source_refs: tuple[SourceRef, ...]
    schema_version: SchemaVersion
    identity_hash: str
    cik: Cik | None = None
    cusip: Cusip | None = None
    isin: Isin | None = None
    valid_to: UtcTimestamp | None = None

    def __post_init__(self) -> None:
        _validate_identity_fields(
            {
                field.name: getattr(self, field.name)
                for field in fields(SecurityIdentityRecord)
                if field.name != "identity_hash"
            }
        )
        if type(self.identity_hash) is not str or _HASH_TEXT.fullmatch(self.identity_hash) is None:
            raise ValueError("identity hash must be a SHA-256 digest")
        if self.identity_hash != self.compute_hash():
            raise ValueError("identity hash does not match frozen record content")

    @property
    def interval(self) -> ValidityInterval:
        return ValidityInterval(valid_from=self.valid_from, valid_to=self.valid_to)

    @property
    def producer_version(self) -> str:
        return _PRODUCER_VERSION

    def known_at(self, cutoff: UtcTimestamp) -> bool:
        """Return whether the system could know this record at ``cutoff``."""
        if type(cutoff) is not UtcTimestamp:
            raise ValueError("knowledge cutoff requires canonical UTC")
        return self.available_at.value <= cutoff.value

    def valid_at(self, as_of: UtcTimestamp) -> bool:
        """Return whether this identity holds in the real world at ``as_of``."""
        return self.interval.contains(as_of)

    def answers_as_of(self, *, as_of: UtcTimestamp, known_at: UtcTimestamp) -> bool:
        """Return whether this record may answer a point-in-time query.

        Both axes must hold: the record must already be knowable at the
        knowledge cutoff and valid at the real-world instant.
        """
        return self.known_at(known_at) and self.valid_at(as_of)

    def wire(self) -> dict[str, object]:
        """Return the canonical content used for the identity hash."""
        return {
            "security_id": self.security_id.value,
            "symbol": self.symbol.value,
            "exchange": self.exchange.value,
            "asset_class": self.asset_class.value,
            "cik": None if self.cik is None else self.cik.value,
            "cusip": None if self.cusip is None else self.cusip.value,
            "isin": None if self.isin is None else self.isin.value,
            "valid_from": str(self.valid_from),
            "valid_to": None if self.valid_to is None else str(self.valid_to),
            "available_at": str(self.available_at),
            "status": self.status.value,
            "source_refs": [
                {
                    "record_id": ref.record_id,
                    "family": ref.family.value,
                    "record_hash": ref.record_hash,
                }
                for ref in self.source_refs
            ],
            "schema_version": str(self.schema_version),
            "producer_version": self.producer_version,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.identity_hash != self.compute_hash():
            raise ValueError("identity hash does not match frozen record content")
        return True


def build_identity_record(**values: object) -> SecurityIdentityRecord:
    """Build an identity record while deriving, never trusting, its hash."""
    complete: dict[str, object] = dict(values)
    for field in fields(SecurityIdentityRecord):
        complete.setdefault(field.name, field.default)
    body = {name: value for name, value in complete.items() if name != "identity_hash"}
    _validate_identity_fields(body)
    provisional = object.__new__(SecurityIdentityRecord)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "identity_hash", "")
    computed = provisional.compute_hash()
    return SecurityIdentityRecord(**body, identity_hash=computed)  # type: ignore[arg-type]


def producer_version() -> str:
    return _PRODUCER_VERSION
