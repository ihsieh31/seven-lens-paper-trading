"""Reconciliation result contracts shared by the service, ports, and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from seven_lens.domain.value_objects import TradingDate, UtcTimestamp

_MAX_MISMATCHES = 10_000
_MAX_DETAIL_LENGTH = 200


class ReconciliationStatus(StrEnum):
    CLEAN = "CLEAN"
    MISMATCH = "MISMATCH"


class MismatchKind(StrEnum):
    """Closed classification of every reconciliation mismatch."""

    NON_PAPER_ACCOUNT = "NON_PAPER_ACCOUNT"
    UNKNOWN_BROKER_ORDER = "UNKNOWN_BROKER_ORDER"
    MISSING_BROKER_ORDER = "MISSING_BROKER_ORDER"
    PARAMETER_MISMATCH = "PARAMETER_MISMATCH"
    STATUS_MISMATCH = "STATUS_MISMATCH"
    INTENT_STATUS_MISMATCH = "INTENT_STATUS_MISMATCH"
    BROKER_QUERY_FAILURE = "BROKER_QUERY_FAILURE"
    MISSING_LOCAL_FILL = "MISSING_LOCAL_FILL"
    POSITION_QUANTITY_MISMATCH = "POSITION_QUANTITY_MISMATCH"
    POSITION_SYMBOL_MISMATCH = "POSITION_SYMBOL_MISMATCH"
    LOCAL_LEDGER_INVARIANT = "LOCAL_LEDGER_INVARIANT"
    ACCOUNT_ID_MISMATCH = "ACCOUNT_ID_MISMATCH"
    CASH_MISMATCH = "CASH_MISMATCH"
    NAV_MISMATCH = "NAV_MISMATCH"
    BUYING_POWER_MISMATCH = "BUYING_POWER_MISMATCH"
    ACCOUNT_RECONCILIATION_UNAVAILABLE = "ACCOUNT_RECONCILIATION_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class ReconciliationMismatch:
    kind: MismatchKind
    detail: str

    def __post_init__(self) -> None:
        if type(self.kind) is not MismatchKind:
            raise ValueError("mismatch kind must be a MismatchKind")
        if (
            type(self.detail) is not str
            or not self.detail
            or len(self.detail) > _MAX_DETAIL_LENGTH
            or "\x00" in self.detail
        ):
            raise ValueError("mismatch detail must be bounded text")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    run_id: UUID
    trading_date: TradingDate
    status: ReconciliationStatus
    mismatches: tuple[ReconciliationMismatch, ...]
    checked_orders: int
    checked_fills: int
    observed_at: UtcTimestamp

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, UUID) or self.run_id.int == 0:
            raise ValueError("run_id must be a non-nil UUID")
        if not isinstance(self.trading_date, TradingDate):
            raise ValueError("trading_date must be a TradingDate")
        if type(self.status) is not ReconciliationStatus:
            raise ValueError("status must be a ReconciliationStatus")
        if len(self.mismatches) > _MAX_MISMATCHES:
            raise ValueError("reconciliation produced too many mismatches")
        for mismatch in self.mismatches:
            if type(mismatch) is not ReconciliationMismatch:
                raise ValueError("mismatches must be ReconciliationMismatch values")
        if type(self.checked_orders) is not int or self.checked_orders < 0:
            raise ValueError("checked_orders must be a non-negative integer")
        if type(self.checked_fills) is not int or self.checked_fills < 0:
            raise ValueError("checked_fills must be a non-negative integer")
        if not isinstance(self.observed_at, UtcTimestamp):
            raise ValueError("observed_at must be a UtcTimestamp")
        if (self.status is ReconciliationStatus.CLEAN) is bool(self.mismatches):
            raise ValueError("CLEAN requires zero mismatches; MISMATCH requires at least one")

    @classmethod
    def create(
        cls,
        *,
        trading_date: TradingDate,
        mismatches: tuple[ReconciliationMismatch, ...],
        checked_orders: int,
        checked_fills: int,
        observed_at: UtcTimestamp,
        run_id: UUID | None = None,
    ) -> ReconciliationResult:
        return cls(
            run_id=run_id or uuid4(),
            trading_date=trading_date,
            status=(
                ReconciliationStatus.CLEAN if not mismatches else ReconciliationStatus.MISMATCH
            ),
            mismatches=mismatches,
            checked_orders=checked_orders,
            checked_fills=checked_fills,
            observed_at=observed_at,
        )
