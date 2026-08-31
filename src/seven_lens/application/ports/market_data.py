"""Capability-minimal persistence ports for P4-C market snapshots.

These ports are the only storage capabilities the market snapshot assembler
may use.  Snapshots are append-only; reads return immutable records whose
hashes re-verify on construction.
"""

from __future__ import annotations

from typing import Protocol

from seven_lens.market_data.snapshots import MarketSnapshot
from seven_lens.securities.contracts import SecurityId


class MarketSnapshotStore(Protocol):
    """Append-only store for versioned market snapshots."""

    def append(self, snapshot: MarketSnapshot) -> None:
        """Append one snapshot; an identical hash is idempotent."""
        ...

    def get(self, snapshot_hash: str) -> MarketSnapshot | None:
        """Return one exact snapshot by hash, or None."""
        ...

    def latest_for_security(self, security_id: SecurityId) -> MarketSnapshot | None:
        """Return the most recent snapshot for one security, or None."""
        ...

    def snapshots(self) -> tuple[MarketSnapshot, ...]:
        """Return every stored snapshot."""
        ...

    def count(self) -> int:
        """Return the number of distinct stored snapshots."""
        ...
