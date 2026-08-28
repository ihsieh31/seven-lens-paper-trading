"""Capability-minimal persistence ports for the P4-B security master.

These ports are the only storage capabilities the security master may use.
Identity observations, split event lineages, and quarantine decisions are all
append-only; reads are point-in-time safe because stores return immutable
records whose hashes re-verify on construction.  A symbol is never an
identity: ``security_id`` scopes a query exclusively when given, and a symbol
query only selects candidate records for the resolver and quarantine layers.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.securities.contracts import SecurityId, SecurityIdentityRecord, SecuritySymbol
from seven_lens.securities.corporate_actions import CorporateActionRecord
from seven_lens.securities.quarantine import QuarantineDecision


class SecurityIdentityStore(Protocol):
    """Append-only point-in-time identity observations and their heads."""

    def append_identity(self, record: SecurityIdentityRecord) -> AppendOutcome:
        """Append one observation; a later available_at corrects the head."""
        ...

    def identity_records(
        self,
        *,
        security_id: SecurityId | None = None,
        symbol: SecuritySymbol | None = None,
    ) -> tuple[SecurityIdentityRecord, ...]:
        """Return stored observations scoped by identity key.

        ``security_id`` scopes exclusively when given; otherwise ``symbol``
        selects candidate records.  At least one key is required.
        """
        ...


class CorporateActionEventStore(Protocol):
    """Append-only forward/reverse split event lineages under the closed machine."""

    def append_event(
        self, record: CorporateActionRecord, *, previous_record_hash: str | None
    ) -> AppendOutcome:
        """Append one lineage row; the head must match ``previous_record_hash``."""
        ...

    def event_lineage(self, event_id: str) -> tuple[CorporateActionRecord, ...]:
        """Return one lineage from its DETECTED root to its head, or empty."""
        ...

    def security_event_ids(self, security_id: SecurityId) -> tuple[str, ...]:
        """Return every event id ever observed for one security."""
        ...


class QuarantineDecisionStore(Protocol):
    """Append-only content-addressed entry-quarantine decisions."""

    def record_decision(self, decision: QuarantineDecision) -> AppendOutcome:
        """Record one decision; an identical hash is idempotent."""
        ...

    def latest_decision(self, security_id: SecurityId) -> QuarantineDecision | None:
        """Return the most recent decision for one security, or None."""
        ...


class SecurityMasterRepository(
    SecurityIdentityStore, CorporateActionEventStore, QuarantineDecisionStore, Protocol
):
    """Atomic composition of the three P4-B append-only authorities."""

    def transaction(self) -> AbstractContextManager[None]:
        """Provide one transaction boundary for block-before-confirm flows."""
        ...
