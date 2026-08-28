"""Process-local P4-B repository used for public-entry and adversarial tests.

This adapter mirrors the durable repository's append-only semantics.  It is
not a production authority: PostgreSQL remains the required persistence and
ACL evidence for the gate.  Keeping this implementation strict is useful
because the service tests can exercise the public failure ordering without
network, broker, or model capabilities.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Final

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.application.ports.securities import SecurityMasterRepository
from seven_lens.securities.contracts import (
    SecurityId,
    SecurityIdentityRecord,
    SecuritySymbol,
    intervals_overlap,
)
from seven_lens.securities.corporate_actions import (
    CorporateActionRecord,
    validate_lineage,
    validate_transition,
)
from seven_lens.securities.quarantine import QuarantineDecision

_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$")


class InMemorySecurityMasterError(RuntimeError):
    """Raised when the process-local repository detects authority drift."""


class InMemoryConcurrentTransitionError(InMemorySecurityMasterError):
    """Raised when a compare-and-swap append names a stale event head."""


class InMemorySecurityMaster:
    """Strict append-only implementation of the P4-B repository port."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._identity_by_hash: dict[str, SecurityIdentityRecord] = {}
        self._identity_order: list[str] = []
        self._identity_heads: dict[tuple[str, str, str | None], str] = {}
        self._event_by_hash: dict[str, CorporateActionRecord] = {}
        self._event_previous: dict[str, str | None] = {}
        self._event_order: list[str] = []
        self._event_heads: dict[str, str] = {}
        self._decision_by_hash: dict[str, QuarantineDecision] = {}
        self._decision_order: list[str] = []

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Provide rollback semantics for the service's durable block flow."""
        with self._lock:
            snapshot = (
                dict(self._identity_by_hash),
                list(self._identity_order),
                dict(self._identity_heads),
                dict(self._event_by_hash),
                dict(self._event_previous),
                list(self._event_order),
                dict(self._event_heads),
                dict(self._decision_by_hash),
                list(self._decision_order),
            )
            try:
                yield
            except Exception:
                (
                    self._identity_by_hash,
                    self._identity_order,
                    self._identity_heads,
                    self._event_by_hash,
                    self._event_previous,
                    self._event_order,
                    self._event_heads,
                    self._decision_by_hash,
                    self._decision_order,
                ) = snapshot
                raise

    def append_identity(self, record: SecurityIdentityRecord) -> AppendOutcome:
        if type(record) is not SecurityIdentityRecord:
            raise ValueError("only an exact SecurityIdentityRecord can be appended")
        record.verify_integrity()
        with self._lock:
            existing = self._identity_by_hash.get(record.identity_hash)
            if existing is not None:
                _require_same_wire(existing.wire(), record.wire(), "identity")
                return AppendOutcome.IDEMPOTENT_DUPLICATE

            key = _identity_key(record)
            for head_hash in self._identity_heads.values():
                head = self._identity_by_hash[head_hash]
                if (
                    head.security_id == record.security_id
                    and _identity_key(head) != key
                    and intervals_overlap(head.interval, record.interval)
                ):
                    raise InMemorySecurityMasterError(
                        "identity interval overlaps an existing head for the same security"
                    )

            current_hash = self._identity_heads.get(key)
            if current_hash is not None:
                current = self._identity_by_hash[current_hash]
                if (
                    record.available_at.value == current.available_at.value
                    and current.identity_hash != record.identity_hash
                ):
                    raise InMemorySecurityMasterError(
                        "unorderable identity correction at the same available time"
                    )

            self._identity_by_hash[record.identity_hash] = record
            self._identity_order.append(record.identity_hash)
            if current_hash is None:
                self._identity_heads[key] = record.identity_hash
            else:
                current = self._identity_by_hash[current_hash]
                if record.available_at.value > current.available_at.value:
                    self._identity_heads[key] = record.identity_hash
            return AppendOutcome.APPENDED

    def identity_records(
        self,
        *,
        security_id: SecurityId | None = None,
        symbol: SecuritySymbol | None = None,
    ) -> tuple[SecurityIdentityRecord, ...]:
        if security_id is not None and type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        if symbol is not None and type(symbol) is not SecuritySymbol:
            raise ValueError("symbol requires an exact SecuritySymbol")
        if security_id is None and symbol is None:
            raise ValueError("identity query requires security_id or symbol")
        with self._lock:
            records = tuple(
                self._identity_by_hash[identity_hash] for identity_hash in self._identity_order
            )
        selected = tuple(
            record
            for record in records
            if (security_id is None or record.security_id == security_id)
            and (symbol is None or record.symbol == symbol)
        )
        for record in selected:
            try:
                record.verify_integrity()
            except ValueError as error:
                raise InMemorySecurityMasterError(
                    "stored identity record failed integrity re-verification"
                ) from error
        return selected

    def append_event(
        self, record: CorporateActionRecord, *, previous_record_hash: str | None
    ) -> AppendOutcome:
        if type(record) is not CorporateActionRecord:
            raise ValueError("only an exact CorporateActionRecord can be appended")
        record.verify_integrity()
        _validate_hash_or_none(previous_record_hash)
        with self._lock:
            identity = self._identity_by_hash.get(record.security_identity_hash)
            if identity is None or identity.security_id != record.security_id:
                raise InMemorySecurityMasterError(
                    "corporate-action event must reference an existing matching identity version"
                )
            existing = self._event_by_hash.get(record.record_hash)
            if existing is not None:
                _require_same_wire(existing.wire(), record.wire(), "corporate-action")
                if self._event_previous[record.record_hash] != previous_record_hash:
                    raise InMemorySecurityMasterError(
                        "corporate-action record hash was reused with a different predecessor"
                    )
                return AppendOutcome.IDEMPOTENT_DUPLICATE

            if record.state.value == "detected":
                if previous_record_hash is not None:
                    raise InMemorySecurityMasterError(
                        "detected root cannot reference a previous head"
                    )
                if record.event_id in self._event_heads:
                    raise InMemorySecurityMasterError(
                        "corporate-action event lineage already started"
                    )
            else:
                if previous_record_hash is None:
                    raise InMemorySecurityMasterError(
                        "transition requires the previous head record hash"
                    )
                current_hash = self._event_heads.get(record.event_id)
                if current_hash is None:
                    raise InMemorySecurityMasterError("corporate-action event lineage not found")
                if current_hash != previous_record_hash:
                    raise InMemoryConcurrentTransitionError("corporate-action head moved")
                previous = self._event_by_hash.get(previous_record_hash)
                if previous is None:
                    raise InMemorySecurityMasterError("previous event head is not in the event log")
                validate_transition(previous, record)

            self._event_by_hash[record.record_hash] = record
            self._event_previous[record.record_hash] = previous_record_hash
            self._event_order.append(record.record_hash)
            self._event_heads[record.event_id] = record.record_hash
            return AppendOutcome.APPENDED

    def event_lineage(self, event_id: str) -> tuple[CorporateActionRecord, ...]:
        if type(event_id) is not str or _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError("event_id must be a canonical event identifier")
        with self._lock:
            hashes = [
                record_hash
                for record_hash in self._event_order
                if self._event_by_hash[record_hash].event_id == event_id
            ]
            if not hashes:
                return ()
            by_previous = {self._event_previous[record_hash]: record_hash for record_hash in hashes}
            root = [
                record_hash for record_hash in hashes if self._event_previous[record_hash] is None
            ]
            if len(root) != 1:
                raise InMemorySecurityMasterError("event lineage must carry exactly one root")
            chain: list[CorporateActionRecord] = []
            current: str | None = root[0]
            seen: set[str] = set()
            while current is not None:
                if current in seen:
                    raise InMemorySecurityMasterError("event lineage carries a hash cycle")
                seen.add(current)
                record = self._event_by_hash.get(current)
                if record is None:
                    raise InMemorySecurityMasterError("event lineage points to a missing row")
                try:
                    record.verify_integrity()
                except ValueError as error:
                    raise InMemorySecurityMasterError(
                        "stored corporate-action record failed integrity re-verification"
                    ) from error
                chain.append(record)
                current = by_previous.get(current)
            if len(seen) != len(hashes):
                raise InMemorySecurityMasterError("event lineage carries a fork or unreachable row")
            lineage = tuple(chain)
        validate_lineage(lineage)
        return lineage

    def security_event_ids(self, security_id: SecurityId) -> tuple[str, ...]:
        if type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        with self._lock:
            return tuple(
                sorted(
                    {
                        record.event_id
                        for record in self._event_by_hash.values()
                        if record.security_id == security_id
                    }
                )
            )

    def record_decision(self, decision: QuarantineDecision) -> AppendOutcome:
        if type(decision) is not QuarantineDecision:
            raise ValueError("only an exact QuarantineDecision can be recorded")
        decision.verify_integrity()
        with self._lock:
            existing = self._decision_by_hash.get(decision.decision_hash)
            if existing is not None:
                _require_same_wire(existing.wire(), decision.wire(), "quarantine decision")
                return AppendOutcome.IDEMPOTENT_DUPLICATE
            self._decision_by_hash[decision.decision_hash] = decision
            self._decision_order.append(decision.decision_hash)
            return AppendOutcome.APPENDED

    def latest_decision(self, security_id: SecurityId) -> QuarantineDecision | None:
        if type(security_id) is not SecurityId:
            raise ValueError("security_id requires an exact SecurityId")
        with self._lock:
            decisions = [
                self._decision_by_hash[decision_hash]
                for decision_hash in self._decision_order
                if self._decision_by_hash[decision_hash].security_id == security_id
            ]
        for decision in decisions:
            decision.verify_integrity()
        return max(
            decisions,
            key=lambda decision: (decision.decision_at.value, decision.decision_hash),
            default=None,
        )


def _identity_key(record: SecurityIdentityRecord) -> tuple[str, str, str | None]:
    return (
        record.security_id.value,
        str(record.valid_from),
        None if record.valid_to is None else str(record.valid_to),
    )


def _validate_hash_or_none(value: str | None) -> None:
    if value is not None and (type(value) is not str or _HASH_TEXT.fullmatch(value) is None):
        raise ValueError("previous_record_hash must be a SHA-256 digest or None")


def _require_same_wire(first: dict[str, object], second: dict[str, object], kind: str) -> None:
    if first != second:
        raise InMemorySecurityMasterError(f"{kind} hash collision carries different content")


_IN_MEMORY_REPOSITORY_PORT: type[SecurityMasterRepository] = InMemorySecurityMaster
