"""Point-in-time identity resolution over the P4-B security master.

The resolver is pure: it reads immutable identity records and answers one
as-of/known-at query with exactly one typed outcome.  It never returns a bare
``None``, never picks an arbitrary first record, and never treats a symbol as
identity — a symbol is only a query key into stable security ids.  Records
learned after the knowledge cutoff are invisible, and append-only corrections
supersede earlier observations for the same identity interval without ever
rewriting history.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.securities.contracts import (
    SecurityId,
    SecurityIdentityRecord,
    SecuritySymbol,
    intervals_overlap,
)

MAX_RESOLUTION_RECORDS: Final = 4096


class IdentityResolutionStatus(StrEnum):
    """The closed resolver outcomes; uncertainty never degrades to a guess."""

    RESOLVED = "RESOLVED"
    UNKNOWN = "UNKNOWN"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICT = "CONFLICT"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class IdentityQuery:
    """One point-in-time identity query.

    ``as_of`` is the real-world instant; ``known_at`` is the system-knowledge
    cutoff.  At least one of ``security_id`` or ``symbol`` must be given; when
    both are given, ``security_id`` scopes the search and the symbol claim is
    left to the quarantine layer to reconcile.
    """

    as_of: UtcTimestamp
    known_at: UtcTimestamp
    security_id: SecurityId | None = None
    symbol: SecuritySymbol | None = None

    def __post_init__(self) -> None:
        if self.security_id is None and self.symbol is None:
            raise ValueError("query requires an identity key: security_id or symbol")
        if self.security_id is not None and type(self.security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if self.symbol is not None and type(self.symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if type(self.as_of) is not UtcTimestamp:
            raise ValueError("as_of requires canonical UTC")
        if type(self.known_at) is not UtcTimestamp:
            raise ValueError("known_at requires canonical UTC")


@dataclass(frozen=True, slots=True)
class IdentityResolution:
    """One typed resolver outcome; ``record`` is set only when RESOLVED."""

    status: IdentityResolutionStatus
    record: SecurityIdentityRecord | None = None

    def __post_init__(self) -> None:
        if type(self.status) is not IdentityResolutionStatus:
            raise ValueError("status requires an exact IdentityResolutionStatus")
        if self.record is not None and type(self.record) is not SecurityIdentityRecord:
            raise ValueError("record requires an exact SecurityIdentityRecord or None")
        if self.status is not IdentityResolutionStatus.RESOLVED and self.record is not None:
            raise ValueError("only a RESOLVED outcome may carry a record")


def resolve_identity(
    records: tuple[SecurityIdentityRecord, ...], query: IdentityQuery
) -> IdentityResolution:
    """Resolve one point-in-time identity query against immutable records.

    Failure ordering is fixed: validate inputs, re-verify record integrity,
    scope by identity key, hide records not yet knowable, apply append-only
    supersession, reject overlapping intervals, then decide.  Any uncertainty
    fails closed to a typed non-RESOLVED outcome.
    """
    if type(query) is not IdentityQuery:
        raise ValueError("query requires an exact IdentityQuery")
    if type(records) is not tuple or len(records) > MAX_RESOLUTION_RECORDS:
        raise ValueError(
            f"records must be a tuple of at most {MAX_RESOLUTION_RECORDS} identity records"
        )
    if any(type(record) is not SecurityIdentityRecord for record in records):
        raise ValueError("records require exact SecurityIdentityRecord values")
    for record in records:
        try:
            record.verify_integrity()
        except ValueError as error:
            raise ValueError("records failed integrity re-verification") from error

    scoped = tuple(record for record in records if _matches_key(record, query))
    if not scoped:
        return IdentityResolution(status=IdentityResolutionStatus.UNKNOWN)

    visible = tuple(record for record in scoped if record.known_at(query.known_at))
    if not visible:
        # Matching records exist but none are knowable at the cutoff: the
        # caller's view predates the master's knowledge.  Never UNKNOWN here.
        return IdentityResolution(status=IdentityResolutionStatus.STALE)

    heads = _supersession_heads(visible)
    if heads is None:
        return IdentityResolution(status=IdentityResolutionStatus.CONFLICT)

    if _any_interval_conflict(heads):
        return IdentityResolution(status=IdentityResolutionStatus.CONFLICT)

    valid = tuple(record for record in heads if record.valid_at(query.as_of))
    if not valid:
        return IdentityResolution(status=IdentityResolutionStatus.UNKNOWN)

    by_security: dict[str, SecurityIdentityRecord] = {}
    for record in valid:
        if record.security_id.value in by_security:
            return IdentityResolution(status=IdentityResolutionStatus.CONFLICT)
        by_security[record.security_id.value] = record
    if len(by_security) > 1:
        return IdentityResolution(status=IdentityResolutionStatus.AMBIGUOUS)

    return IdentityResolution(status=IdentityResolutionStatus.RESOLVED, record=valid[0])


def _matches_key(record: SecurityIdentityRecord, query: IdentityQuery) -> bool:
    if query.security_id is not None:
        return record.security_id == query.security_id
    return record.symbol == query.symbol


def _interval_key(record: SecurityIdentityRecord) -> tuple[str, str, str | None]:
    return (
        record.security_id.value,
        str(record.valid_from),
        None if record.valid_to is None else str(record.valid_to),
    )


def _supersession_heads(
    visible: tuple[SecurityIdentityRecord, ...],
) -> tuple[SecurityIdentityRecord, ...] | None:
    """Collapse append-only corrections into one current head per interval.

    Records sharing one security and one exact validity interval are one
    lineage: the latest ``available_at`` wins.  Two distinct records that are
    knowable at the same instant for the same lineage are unorderable and
    return ``None`` so the caller fails closed with CONFLICT.
    """
    groups: dict[tuple[str, str, str | None], list[SecurityIdentityRecord]] = {}
    for record in visible:
        groups.setdefault(_interval_key(record), []).append(record)

    heads: list[SecurityIdentityRecord] = []
    for group in groups.values():
        unique = {record.identity_hash: record for record in group}
        if len(unique) == 1:
            heads.append(next(iter(unique.values())))
            continue
        latest = max(record.available_at.value for record in unique.values())
        contenders = [record for record in unique.values() if record.available_at.value == latest]
        if len(contenders) != 1:
            return None
        heads.append(contenders[0])
    return tuple(heads)


def _any_interval_conflict(heads: tuple[SecurityIdentityRecord, ...]) -> bool:
    """Return whether one security carries overlapping validity heads."""
    by_security: dict[str, list[SecurityIdentityRecord]] = {}
    for record in heads:
        by_security.setdefault(record.security_id.value, []).append(record)
    for group in by_security.values():
        group.sort(key=lambda record: record.valid_from.value)
        for earlier, later in pairwise(group):
            if intervals_overlap(earlier.interval, later.interval):
                return True
    return False
