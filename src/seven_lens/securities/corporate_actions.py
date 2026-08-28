"""Point-in-time corporate-action records for the P4-B security master.

Only forward and reverse splits exist here; every other corporate action is
unsupported and must fail closed upstream.  Ratios are exact positive
rationals, never floats.  An event lineage is append-only: it begins at
``DETECTED`` and moves only along the closed transition table, and every
transition re-verifies the previous head, the immutable event facts (identity
version, type, ratio, dates), and decision-time monotonicity.  Illegal
transitions are rejected at the record level, not only in a service layer.
P4-B never marks an event ``EXITED``; that requires P4-E/P7 evidence.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Final

from seven_lens.domain.value_objects import SchemaVersion, TradingDate, UtcTimestamp
from seven_lens.securities.contracts import MAX_SOURCE_REFS, SecurityId, SourceRef

_HASH_DOMAIN: Final = b"seven-lens.p4b.corporate-action.v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")
_PRODUCER_VERSION: Final = "p4b.corporate-actions.v1"

MAX_LINEAGE_RECORDS: Final = 4096


class CorporateActionType(StrEnum):
    """Closed corporate-action types; only splits are representable in P4-B."""

    FORWARD_SPLIT = "forward_split"
    REVERSE_SPLIT = "reverse_split"


class CorporateActionState(StrEnum):
    """Closed lifecycle states for one split event lineage."""

    DETECTED = "detected"
    ENTRY_BLOCKED = "entry_blocked"
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    EFFECTIVE_PENDING_RECONCILIATION = "effective_pending_reconciliation"


class IllegalTransitionError(ValueError):
    """A corporate-action transition violated the closed state machine."""


_ALLOWED_TRANSITIONS: Final[frozenset[tuple[CorporateActionState, CorporateActionState]]] = (
    frozenset(
        {
            (CorporateActionState.DETECTED, CorporateActionState.ENTRY_BLOCKED),
            (CorporateActionState.ENTRY_BLOCKED, CorporateActionState.CONFIRMED),
            (CorporateActionState.DETECTED, CorporateActionState.REVIEW_REQUIRED),
            (CorporateActionState.ENTRY_BLOCKED, CorporateActionState.REVIEW_REQUIRED),
            (CorporateActionState.CONFIRMED, CorporateActionState.REVIEW_REQUIRED),
            (
                CorporateActionState.CONFIRMED,
                CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION,
            ),
        }
    )
)

_TERMINAL_STATES: Final[frozenset[CorporateActionState]] = frozenset(
    {
        CorporateActionState.REVIEW_REQUIRED,
        CorporateActionState.EFFECTIVE_PENDING_RECONCILIATION,
    }
)

_IMMUTABLE_EVENT_FACTS: Final = (
    "security_id",
    "security_identity_hash",
    "action_type",
    "ratio",
    "declared_at",
    "ex_date",
    "effective_date",
)


def parse_action_type(text: object) -> CorporateActionType:
    """Parse only the two closed split types; anything else fails closed."""
    if type(text) is not str:
        raise ValueError("action type text must be a string")
    try:
        return CorporateActionType(text)
    except ValueError:
        raise ValueError(f"unsupported corporate action type: {text!r}") from None


def allowed_transitions() -> frozenset[tuple[CorporateActionState, CorporateActionState]]:
    """Return the closed transition table; nothing outside it is legal."""
    return _ALLOWED_TRANSITIONS


def is_legal_transition(
    *, from_state: CorporateActionState, to_state: CorporateActionState
) -> bool:
    """Return whether one state change is part of the closed machine."""
    if type(from_state) is not CorporateActionState or type(to_state) is not CorporateActionState:
        raise ValueError("transition states require exact CorporateActionState values")
    return (from_state, to_state) in _ALLOWED_TRANSITIONS


def is_terminal(state: CorporateActionState) -> bool:
    """Return whether a state has no outgoing transition in P4-B."""
    if type(state) is not CorporateActionState:
        raise ValueError("terminal check requires an exact CorporateActionState")
    return state in _TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class SplitRatio:
    """An exact positive rational split ratio; never a float.

    Direct construction requires an already-normalized pair; use
    ``from_fraction`` or ``from_decimal_text`` to build ratios safely.
    """

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if type(self.numerator) is not int:
            raise ValueError("split ratio numerator must be an exact int, never a float")
        if type(self.denominator) is not int:
            raise ValueError("split ratio denominator must be an exact int, never a float")
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("split ratio must be strictly positive")
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("split ratio must be normalized; use SplitRatio.from_fraction")

    @classmethod
    def from_fraction(cls, *, numerator: int, denominator: int) -> SplitRatio:
        """Build a normalized ratio from an exact positive integer pair."""
        if type(numerator) is not int:
            raise ValueError("split ratio numerator must be an exact int, never a float")
        if type(denominator) is not int:
            raise ValueError("split ratio denominator must be an exact int, never a float")
        if numerator <= 0 or denominator <= 0:
            raise ValueError("split ratio must be strictly positive")
        scale = math.gcd(numerator, denominator)
        return cls(numerator=numerator // scale, denominator=denominator // scale)

    @classmethod
    def from_decimal(cls, value: Decimal) -> SplitRatio:
        """Build a ratio from an exact finite positive Decimal; floats fail."""
        if type(value) is not Decimal:
            raise ValueError("split ratio decimal input must be an exact Decimal, never a float")
        if not value.is_finite():
            raise ValueError("split ratio must be finite; NaN and Infinity are rejected")
        if value <= 0:
            raise ValueError("split ratio must be strictly positive")
        numerator, denominator = value.as_integer_ratio()
        return cls.from_fraction(numerator=numerator, denominator=denominator)

    @classmethod
    def from_decimal_text(cls, text: str) -> SplitRatio:
        """Parse exact decimal literal text such as ``"1.5"`` or ``"0.25"``."""
        if type(text) is not str:
            raise ValueError("split ratio text must be a string")
        try:
            value = Decimal(text)
        except InvalidOperation as error:
            raise ValueError("split ratio text must be an exact decimal literal") from error
        return cls.from_decimal(value)

    def wire(self) -> dict[str, int]:
        """Return the exact rational form used in canonical hashes."""
        return {"numerator": self.numerator, "denominator": self.denominator}


def _validate_action_fields(values: Mapping[str, object]) -> None:
    """Fail closed on any field that violates the corporate-action contract."""
    event_id = values.get("event_id")
    if type(event_id) is not str or _EVENT_ID.fullmatch(event_id) is None:
        raise ValueError("event id must be a canonical event identifier")
    if type(values.get("security_id")) is not SecurityId:
        raise ValueError("security_id requires an exact SecurityId")
    identity_hash = values.get("security_identity_hash")
    if type(identity_hash) is not str or _HASH_TEXT.fullmatch(identity_hash) is None:
        raise ValueError("security identity hash must be a SHA-256 digest")
    if type(values.get("action_type")) is not CorporateActionType:
        raise ValueError("action_type requires an exact CorporateActionType")
    if type(values.get("ratio")) is not SplitRatio:
        raise ValueError("ratio requires an exact SplitRatio, never a float")
    declared_at = values.get("declared_at")
    if type(declared_at) is not UtcTimestamp:
        raise ValueError("declared_at requires canonical UTC")
    ex_date = values.get("ex_date")
    if type(ex_date) is not TradingDate:
        raise ValueError("ex_date requires an exact TradingDate")
    effective_date = values.get("effective_date")
    if type(effective_date) is not TradingDate:
        raise ValueError("effective_date requires an exact TradingDate")
    if ex_date.value < declared_at.value.date():
        raise ValueError("ex date cannot precede the declaration date")
    if effective_date.value < ex_date.value:
        raise ValueError("effective date cannot precede the ex date")
    available_at = values.get("available_at")
    if type(available_at) is not UtcTimestamp:
        raise ValueError("available_at requires canonical UTC")
    if available_at.value < declared_at.value:
        raise ValueError("available_at cannot precede the declaration time")
    if type(values.get("state")) is not CorporateActionState:
        raise ValueError("state requires an exact CorporateActionState")
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
class CorporateActionRecord:
    """One immutable append-only row of a split event lineage.

    The first row of a lineage is its ``DETECTED`` observation; later rows are
    transitions that repeat every immutable event fact and pin the same
    security identity version.  ``available_at`` is the decision time of the
    row and must never regress along a lineage.
    """

    event_id: str
    security_id: SecurityId
    security_identity_hash: str
    action_type: CorporateActionType
    ratio: SplitRatio
    declared_at: UtcTimestamp
    ex_date: TradingDate
    effective_date: TradingDate
    available_at: UtcTimestamp
    state: CorporateActionState
    source_refs: tuple[SourceRef, ...]
    schema_version: SchemaVersion
    record_hash: str

    def __post_init__(self) -> None:
        _validate_action_fields(
            {
                field.name: getattr(self, field.name)
                for field in fields(CorporateActionRecord)
                if field.name != "record_hash"
            }
        )
        if type(self.record_hash) is not str or _HASH_TEXT.fullmatch(self.record_hash) is None:
            raise ValueError("record hash must be a SHA-256 digest")
        if self.record_hash != self.compute_hash():
            raise ValueError("record hash does not match frozen corporate-action content")

    @property
    def producer_version(self) -> str:
        return _PRODUCER_VERSION

    def known_at(self, cutoff: UtcTimestamp) -> bool:
        """Return whether the system could know this row at ``cutoff``."""
        if type(cutoff) is not UtcTimestamp:
            raise ValueError("knowledge cutoff requires canonical UTC")
        return self.available_at.value <= cutoff.value

    def wire(self) -> dict[str, object]:
        """Return the canonical content used for the record hash."""
        return {
            "event_id": self.event_id,
            "security_id": self.security_id.value,
            "security_identity_hash": self.security_identity_hash,
            "action_type": self.action_type.value,
            "ratio": self.ratio.wire(),
            "declared_at": str(self.declared_at),
            "ex_date": str(self.ex_date),
            "effective_date": str(self.effective_date),
            "available_at": str(self.available_at),
            "state": self.state.value,
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
        if self.record_hash != self.compute_hash():
            raise ValueError("record hash does not match frozen corporate-action content")
        return True


def build_corporate_action_record(**values: object) -> CorporateActionRecord:
    """Build a corporate-action row while deriving, never trusting, its hash."""
    body = {name: value for name, value in values.items() if name != "record_hash"}
    _validate_action_fields(body)
    provisional = object.__new__(CorporateActionRecord)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "record_hash", "")
    computed = provisional.compute_hash()
    return CorporateActionRecord(**body, record_hash=computed)  # type: ignore[arg-type]


def validate_transition(previous: CorporateActionRecord, candidate: CorporateActionRecord) -> None:
    """Validate one append-only transition against its previous head.

    Re-verifies integrity of both rows, event continuity, every immutable
    event fact including the pinned identity version, the closed transition
    table, and decision-time monotonicity.  Raises ``IllegalTransitionError``
    on any violation; nothing here is left to a service layer.
    """
    if type(previous) is not CorporateActionRecord or type(candidate) is not CorporateActionRecord:
        raise ValueError("transition validation requires exact CorporateActionRecord values")
    previous.verify_integrity()
    candidate.verify_integrity()
    if previous.event_id != candidate.event_id:
        raise IllegalTransitionError("transition must stay within one corporate-action event")
    for name in _IMMUTABLE_EVENT_FACTS:
        if getattr(previous, name) != getattr(candidate, name):
            raise IllegalTransitionError(f"transition cannot change immutable event fact: {name}")
    if not is_legal_transition(from_state=previous.state, to_state=candidate.state):
        raise IllegalTransitionError(
            "illegal corporate-action transition: "
            f"{previous.state.value} -> {candidate.state.value}"
        )
    if candidate.available_at.value < previous.available_at.value:
        raise IllegalTransitionError("transition decision time cannot precede the previous head")


def validate_lineage(records: tuple[CorporateActionRecord, ...]) -> CorporateActionRecord:
    """Validate one bounded append-only event lineage and return its head.

    The lineage must begin at ``DETECTED`` and every consecutive pair must be
    a legal transition; the returned head is the current state of the event.
    """
    if type(records) is not tuple or not records or len(records) > MAX_LINEAGE_RECORDS:
        raise ValueError(
            "lineage must be a non-empty tuple of at most "
            f"{MAX_LINEAGE_RECORDS} CorporateActionRecord values"
        )
    if any(type(record) is not CorporateActionRecord for record in records):
        raise ValueError("lineage requires exact CorporateActionRecord values")
    for record in records:
        record.verify_integrity()
    if records[0].state is not CorporateActionState.DETECTED:
        raise IllegalTransitionError("corporate-action lineage must begin at DETECTED")
    for earlier, later in pairwise(records):
        validate_transition(earlier, later)
    return records[-1]


def producer_version() -> str:
    return _PRODUCER_VERSION
