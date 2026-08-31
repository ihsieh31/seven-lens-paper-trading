"""Capability-minimal persistence ports for P4-C universe and candidate sets.

These ports are the only storage capabilities the universe builder and
screening funnel may use.  Snapshots are append-only; reads return immutable
records whose hashes re-verify on construction.
"""

from __future__ import annotations

from typing import Protocol

from seven_lens.application.ports.p4_source_records import AppendOutcome
from seven_lens.screening.contracts import CandidateSet, FeatureVector, SectorAssignment
from seven_lens.screening.funnel import ClusterResult
from seven_lens.universe.contracts import UniverseSnapshot


class UniverseSnapshotStore(Protocol):
    """Append-only store for versioned universe snapshots."""

    def append(self, snapshot: UniverseSnapshot) -> None:
        """Append one snapshot; an identical hash is idempotent."""
        ...

    def get(self, universe_hash: str) -> UniverseSnapshot | None:
        """Return one exact snapshot by hash, or None."""
        ...

    def latest(self) -> UniverseSnapshot | None:
        """Return the most recent universe snapshot, or None."""
        ...

    def snapshots(self) -> tuple[UniverseSnapshot, ...]:
        """Return every stored snapshot."""
        ...

    def count(self) -> int:
        """Return the number of distinct stored snapshots."""
        ...


class FeatureVectorStore(Protocol):
    """Append-only store for factor feature vectors."""

    def append(self, vector: FeatureVector) -> None:
        """Append one vector; an identical hash is idempotent."""
        ...

    def get(self, feature_hash: str) -> FeatureVector | None:
        """Return one exact vector by hash, or None."""
        ...

    def vectors_for_as_of(self, as_of: object) -> tuple[FeatureVector, ...]:
        """Return vectors for a given as-of instant."""
        ...


class SectorAssignmentStore(Protocol):
    """Append-only store for point-in-time SEC SIC assignments."""

    def append(self, assignment: SectorAssignment) -> AppendOutcome:
        """Append one assignment; an identical hash is idempotent."""
        ...

    def get(self, assignment_hash: str) -> SectorAssignment | None:
        """Return one exact assignment by hash, or None."""
        ...


class CandidateSetStore(Protocol):
    """Append-only store for candidate sets."""

    def append(self, candidate_set: CandidateSet) -> None:
        """Append one set; an identical hash is idempotent."""
        ...

    def get(self, candidate_hash: str) -> CandidateSet | None:
        """Return one exact set by hash, or None."""
        ...

    def latest(self) -> CandidateSet | None:
        """Return the most recent candidate set, or None."""
        ...


class ClusterResultStore(Protocol):
    """Append-only store for durable correlation-cluster results."""

    def append(self, result: ClusterResult) -> AppendOutcome:
        """Append one cluster result; an identical cluster is idempotent."""
        ...

    def get(self, cluster_id: str) -> ClusterResult | None:
        """Return one exact cluster result by its content-derived id."""
        ...

    def results_for_as_of(self, as_of: object) -> tuple[ClusterResult, ...]:
        """Return one result per cluster for a given as-of instant."""
        ...

    def count(self) -> int:
        """Return the number of distinct stored clusters."""
        ...
