"""Capability-minimal application port for authoritative model-call audit writes."""

from __future__ import annotations

from typing import Protocol

from seven_lens.analysis.model_audit import (
    CanonicalModelCallResult,
    ModelCallAuditRecord,
    ModelCallClaim,
    ModelCallClaimResult,
    StoredModelCallAttempt,
)
from seven_lens.domain.value_objects import RunId


class ModelCallAuditError(RuntimeError):
    """Raised when an attempt cannot obtain durable audit authority."""


class ModelCallAuditPort(Protocol):
    def claim(self, claim: ModelCallClaim) -> ModelCallClaimResult:
        """Commit intent before network; only CLAIMED authorizes one provider request."""
        ...

    def load(self, call_id: RunId) -> StoredModelCallAttempt | None:
        """Load before network so a closed attempt is replayed without a duplicate call."""
        ...

    def persist(
        self,
        record: ModelCallAuditRecord,
        result: CanonicalModelCallResult | None,
    ) -> bool:
        """Atomically close audit + parsed output; False means an exact replay exists."""
        ...
