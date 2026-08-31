"""Point-in-time versioned US-equity universe snapshot contracts.

A ``UniverseSnapshot`` is the immutable, hash-bound result of a monthly
universe assembly.  It carries every security that was considered, the
eligibility decision, and the exact reason for each exclusion.  The snapshot
is ordered by canonical stable security identity, never by provider or DB
return order.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Final, TypedDict, cast

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.screening.reasons import ClosedReason
from seven_lens.securities.contracts import SecurityId, SecuritySymbol
from seven_lens.securities.quarantine import MAX_EVENT_LINEAGES

_HASH_DOMAIN: Final = b"seven-lens.p4c.universe-snapshot.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_PRODUCER_VERSION: Final = "p4c.universe.v1"
_UNIVERSE_SNAPSHOT_AUTHORITY: Final = object()
_UNIVERSE_SNAPSHOT_READBACK_AUTHORITY: Final = object()

_MAX_UNIVERSE_ENTRIES: Final = 10_000
MAX_UNIVERSE_SNAPSHOT_ITEMS: Final = _MAX_UNIVERSE_ENTRIES
MAX_UNIVERSE_SNAPSHOT_BYTES: Final = 16 * 1024 * 1024
_MAX_MASTER_VERSION_BYTES: Final = 128
_MASTER_VERSION: Final = re.compile(r"^p4b\.securities\.v1:[0-9a-f]{64}$")
_EVENT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


class WholeShareFeasibility(StrEnum):
    """Whether the security can receive at least one whole share under policy.

    ``NOT_EVALUATED`` is used in P4-C (no portfolio/quantity capability);
    P4-D/E set the definitive value.
    """

    FEASIBLE = "FEASIBLE"
    NOT_FEASIBLE = "NOT_FEASIBLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class _UniverseSnapshotBody(TypedDict):
    """Typed constructor body shared by trusted build and readback paths."""

    as_of: TradingDate
    known_at: UtcTimestamp
    security_master_version: str
    market_snapshot_refs: tuple[str, ...]
    entries: tuple[UniverseEntry, ...]
    policy_hash: str
    schema_version: SchemaVersion
    producer_version: str


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    """One security in a universe snapshot with its eligibility decision."""

    security_id: SecurityId
    symbol: SecuritySymbol
    eligible: bool
    reason: ClosedReason | None
    identity_hash: str | None
    master_version: str | None
    market_snapshot_hash: str | None
    whole_share_feasibility: WholeShareFeasibility
    quarantine_decision_hash: str | None = None
    quarantine_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.eligible) is not bool:
            raise ValueError("eligible requires an exact bool")
        if self.reason is not None and type(self.reason) is not ClosedReason:
            raise ValueError("reason requires an exact ClosedReason or None")
        if self.eligible and self.reason is not None:
            raise ValueError("eligible entries must have a null reason")
        if not self.eligible and self.reason is None:
            raise ValueError("ineligible entries must have a non-null reason")
        if self.eligible and (
            self.identity_hash is None
            or self.master_version is None
            or self.market_snapshot_hash is None
        ):
            raise ValueError("eligible entries require closed identity and market references")
        if self.identity_hash is not None and (
            type(self.identity_hash) is not str or _HASH_TEXT.fullmatch(self.identity_hash) is None
        ):
            raise ValueError("identity_hash must be a SHA-256 digest or None")
        if self.master_version is not None and (
            type(self.master_version) is not str
            or len(self.master_version.encode("utf-8")) > _MAX_MASTER_VERSION_BYTES
            or _MASTER_VERSION.fullmatch(self.master_version) is None
        ):
            raise ValueError("master_version must be the bounded P4-B version or None")
        if self.market_snapshot_hash is not None and (
            type(self.market_snapshot_hash) is not str
            or _HASH_TEXT.fullmatch(self.market_snapshot_hash) is None
        ):
            raise ValueError("market_snapshot_hash must be a SHA-256 digest or None")
        if self.quarantine_decision_hash is not None and (
            type(self.quarantine_decision_hash) is not str
            or _HASH_TEXT.fullmatch(self.quarantine_decision_hash) is None
        ):
            raise ValueError("quarantine_decision_hash must be a SHA-256 digest or None")
        if type(self.quarantine_event_ids) is not tuple or any(
            type(event_id) is not str or not event_id for event_id in self.quarantine_event_ids
        ):
            raise ValueError("quarantine_event_ids must be a tuple of non-empty strings")
        if self.quarantine_event_ids != tuple(sorted(set(self.quarantine_event_ids))):
            raise ValueError("quarantine_event_ids must be sorted and unique")
        if self.quarantine_decision_hash is None and self.quarantine_event_ids:
            raise ValueError("quarantine event ids require a decision hash")
        if self.eligible and self.quarantine_decision_hash is None:
            raise ValueError("eligible entries require a quarantine decision reference")
        if type(self.whole_share_feasibility) is not WholeShareFeasibility:
            raise ValueError("whole_share_feasibility requires an exact WholeShareFeasibility")
        if self.whole_share_feasibility is not WholeShareFeasibility.NOT_EVALUATED:
            raise ValueError("P4-C universe entries cannot evaluate whole-share feasibility")
        if len(self.quarantine_event_ids) > MAX_EVENT_LINEAGES:
            raise ValueError("quarantine_event_ids exceed the P4-B event bound")
        if any(_EVENT_ID.fullmatch(event_id) is None for event_id in self.quarantine_event_ids):
            raise ValueError("quarantine_event_ids require canonical event identifiers")


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    """One versioned, immutable, hash-bound universe snapshot.

    ``entries`` are ordered by canonical stable security id (sorted ascending).
    ``eligible`` counts are derived from the entry list.  The snapshot carries
    the policy hash and the universe hash over the ordered content.
    """

    as_of: TradingDate
    known_at: UtcTimestamp
    security_master_version: str
    market_snapshot_refs: tuple[str, ...]
    entries: tuple[UniverseEntry, ...]
    policy_hash: str
    schema_version: SchemaVersion
    producer_version: str
    universe_hash: str
    _authority: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._authority is not _UNIVERSE_SNAPSHOT_AUTHORITY
            and self._authority is not _UNIVERSE_SNAPSHOT_READBACK_AUTHORITY
        ):
            raise ValueError("UniverseSnapshot must be produced by a trusted authority")
        if type(self.as_of) is not TradingDate:
            raise ValueError("as_of requires an exact TradingDate")
        if self.as_of.value.day != 1:
            raise ValueError("universe as_of must be the first day of a calendar month")
        if type(self.known_at) is not UtcTimestamp:
            raise ValueError("known_at requires canonical UTC")
        if self.known_at.value.date() > self.as_of.value:
            raise ValueError("known_at cannot be after the universe as_of date")
        if (
            type(self.security_master_version) is not str
            or len(self.security_master_version.encode("utf-8")) > _MAX_MASTER_VERSION_BYTES
            or _MASTER_VERSION.fullmatch(self.security_master_version) is None
        ):
            raise ValueError("security_master_version must be the bounded P4-B version")
        if type(self.market_snapshot_refs) is not tuple:
            raise ValueError("market_snapshot_refs must be a tuple")
        if len(self.market_snapshot_refs) > MAX_UNIVERSE_SNAPSHOT_ITEMS:
            raise ValueError("market_snapshot_refs exceed the universe snapshot item bound")
        if any(
            type(ref) is not str or _HASH_TEXT.fullmatch(ref) is None
            for ref in self.market_snapshot_refs
        ):
            raise ValueError("market_snapshot_refs must contain SHA-256 digests")
        if self.market_snapshot_refs != tuple(sorted(set(self.market_snapshot_refs))):
            raise ValueError("market_snapshot_refs must be sorted and unique")
        if (
            type(self.entries) is not tuple
            or len(self.entries) > MAX_UNIVERSE_SNAPSHOT_ITEMS
            or any(type(e) is not UniverseEntry for e in self.entries)
        ):
            raise ValueError(
                "entries must be a tuple of at most "
                f"{MAX_UNIVERSE_SNAPSHOT_ITEMS} UniverseEntry values"
            )
        if self.entries != tuple(sorted(self.entries, key=lambda e: e.security_id.value)):
            raise ValueError("entries must be sorted by canonical security id")
        security_ids = [entry.security_id.value for entry in self.entries]
        if len(security_ids) != len(set(security_ids)):
            raise ValueError("entries must not repeat a security")
        if type(self.policy_hash) is not str or _HASH_TEXT.fullmatch(self.policy_hash) is None:
            raise ValueError("policy_hash must be a SHA-256 digest")
        if type(self.schema_version) is not SchemaVersion:
            raise ValueError("schema_version requires an exact SchemaVersion")
        if type(self.producer_version) is not str or self.producer_version != _PRODUCER_VERSION:
            raise ValueError("producer_version is not approved")
        if type(self.universe_hash) is not str or _HASH_TEXT.fullmatch(self.universe_hash) is None:
            raise ValueError("universe_hash must be a SHA-256 digest")
        if self.universe_hash != self.compute_hash():
            raise ValueError("universe_hash does not match frozen content")

    @property
    def eligible_entries(self) -> tuple[UniverseEntry, ...]:
        return tuple(e for e in self.entries if e.eligible)

    @property
    def ineligible_entries(self) -> tuple[UniverseEntry, ...]:
        return tuple(e for e in self.entries if not e.eligible)

    def wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "as_of": str(self.as_of),
            "known_at": str(self.known_at),
            "security_master_version": self.security_master_version,
            "market_snapshot_refs": list(self.market_snapshot_refs),
            "entries": [
                {
                    "security_id": e.security_id.value,
                    "symbol": e.symbol.value,
                    "eligible": e.eligible,
                    "reason": None if e.reason is None else e.reason.value,
                    "identity_hash": e.identity_hash,
                    "master_version": e.master_version,
                    "market_snapshot_hash": e.market_snapshot_hash,
                    "whole_share_feasibility": e.whole_share_feasibility.value,
                    "quarantine_decision_hash": e.quarantine_decision_hash,
                    "quarantine_event_ids": list(e.quarantine_event_ids),
                }
                for e in self.entries
            ],
            "policy_hash": self.policy_hash,
            "schema_version": str(self.schema_version),
            "producer_version": self.producer_version,
        }
        _canonical_universe_wire_bytes(wire)
        return wire

    def compute_hash(self) -> str:
        canonical = _canonical_universe_wire_bytes(self.wire())
        return sha256(_HASH_DOMAIN + canonical).hexdigest()

    def verify_integrity(self) -> bool:
        if self.universe_hash != self.compute_hash():
            raise ValueError("universe_hash does not match frozen content")
        return True


def _canonical_universe_wire_bytes(wire: dict[str, object]) -> bytes:
    """Serialize the universe wire and enforce its UTF-8 resource bound."""
    canonical = json.dumps(
        wire, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(canonical) > MAX_UNIVERSE_SNAPSHOT_BYTES:
        raise ValueError("universe snapshot canonical wire exceeds the byte bound")
    return canonical


def _typed_universe_snapshot_body(values: dict[str, object]) -> _UniverseSnapshotBody:
    """Cast the validated dynamic body to the dataclass constructor shape."""
    return cast(_UniverseSnapshotBody, values)


def _build_universe_snapshot(*, authority: object, **values: object) -> UniverseSnapshot:
    """Build a universe snapshot from the trusted deterministic builder."""
    if authority is not _UNIVERSE_SNAPSHOT_AUTHORITY:
        raise ValueError("universe snapshot construction requires trusted builder authority")
    from dataclasses import MISSING
    from dataclasses import fields as dc_fields

    body = {name: value for name, value in values.items() if name != "universe_hash"}
    for dataclass_field in dc_fields(UniverseSnapshot):
        if (
            dataclass_field.name not in body
            and dataclass_field.name != "universe_hash"
            and dataclass_field.default is not MISSING
        ):
            body[dataclass_field.name] = dataclass_field.default
    # The capability is supplied below and must never be accepted from the
    # caller or duplicated through the dataclass default.
    body.pop("_authority", None)
    typed_body = _typed_universe_snapshot_body(body)
    return UniverseSnapshot(
        **typed_body,
        universe_hash=_derive_universe_hash(**body),
        _authority=_UNIVERSE_SNAPSHOT_AUTHORITY,
    )


def build_universe_snapshot(**values: object) -> UniverseSnapshot:
    """Reject arbitrary public construction of a universe authority."""
    del values
    raise ValueError("build_universe_snapshot is a trusted builder-only API")


def _reconstruct_universe_snapshot(
    *, authority: object, universe_hash: str, **values: object
) -> UniverseSnapshot:
    """Reconstruct one snapshot after a trusted persistence readback."""
    if authority is not _UNIVERSE_SNAPSHOT_READBACK_AUTHORITY:
        raise ValueError("universe snapshot reconstruction requires trusted readback authority")
    if type(universe_hash) is not str or _HASH_TEXT.fullmatch(universe_hash) is None:
        raise ValueError("universe_hash must be a SHA-256 digest")
    typed_body = _typed_universe_snapshot_body(
        {name: value for name, value in values.items() if name != "universe_hash"}
    )
    return UniverseSnapshot(
        **typed_body,
        universe_hash=universe_hash,
        _authority=_UNIVERSE_SNAPSHOT_READBACK_AUTHORITY,
    )


def _derive_universe_hash(**body: object) -> str:
    provisional = object.__new__(UniverseSnapshot)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "universe_hash", "")
    return provisional.compute_hash()
