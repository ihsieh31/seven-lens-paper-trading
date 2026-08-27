"""Deterministic, fail-closed emergency event verification.

This is an implemented contract with production composition intentionally
deferred to the P4 candidate/risk gate. It must not be imported into the P2
execution path merely to create a production call graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.contracts import SourceFamily, SourceKind

_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class EventKind(StrEnum):
    PRICE_VOLUME = "PRICE_VOLUME"
    NEWS = "NEWS"


class EventReason(StrEnum):
    VERIFIED = "VERIFIED"
    INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
    SOURCE_FAMILY_COLLISION = "SOURCE_FAMILY_COLLISION"
    STALE = "STALE"
    FUTURE = "FUTURE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DATA_CONFLICT = "DATA_CONFLICT"
    PRIMARY_OFFICIAL = "PRIMARY_OFFICIAL"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True, slots=True)
class MarketObservation:
    observation_id: str
    symbol: str
    source_family: SourceFamily
    observed_at: UtcTimestamp
    price: Decimal
    volume: int
    fresh_until: UtcTimestamp

    def __post_init__(self) -> None:
        if (
            type(self.observation_id) is not str
            or not self.observation_id
            or len(self.observation_id) > 96
        ):
            raise ValueError("invalid observation identity")
        if type(self.symbol) is not str or _SYMBOL.fullmatch(self.symbol) is None:
            raise ValueError("invalid symbol")
        if type(self.source_family) is not SourceFamily:
            raise ValueError("source_family requires an exact enum")
        if type(self.observed_at) is not UtcTimestamp or type(self.fresh_until) is not UtcTimestamp:
            raise ValueError("observation timestamps require canonical UTC")
        if type(self.price) is not Decimal or not self.price.is_finite() or self.price <= 0:
            raise ValueError("price must be a positive Decimal")
        if type(self.volume) is not int or self.volume < 0:
            raise ValueError("volume must be a non-negative exact integer")
        if self.fresh_until.value < self.observed_at.value:
            raise ValueError("fresh_until precedes observation")


@dataclass(frozen=True, slots=True)
class EventCandidate:
    event_id: str
    kind: EventKind
    symbol: str
    as_of: UtcTimestamp
    observations: tuple[MarketObservation, ...] = ()
    news_source_families: tuple[SourceFamily, ...] = ()
    news_source_kind: SourceKind | None = None
    news_primary: bool = False
    news_conflict: bool = False

    def __post_init__(self) -> None:
        if type(self.event_id) is not str or not self.event_id or len(self.event_id) > 96:
            raise ValueError("invalid event identity")
        if type(self.kind) is not EventKind or type(self.as_of) is not UtcTimestamp:
            raise ValueError("invalid event kind/time")
        if type(self.symbol) is not str or _SYMBOL.fullmatch(self.symbol) is None:
            raise ValueError("invalid event symbol")
        if (
            type(self.observations) is not tuple
            or len(self.observations) > 32
            or any(type(x) is not MarketObservation for x in self.observations)
        ):
            raise ValueError("invalid observation tuple")
        if type(self.news_source_families) is not tuple or any(
            type(x) is not SourceFamily for x in self.news_source_families
        ):
            raise ValueError("invalid news families")
        if len(self.news_source_families) != len(set(self.news_source_families)):
            raise ValueError("duplicate news family")
        if self.news_source_kind is not None and type(self.news_source_kind) is not SourceKind:
            raise ValueError("invalid news source kind")
        if type(self.news_primary) is not bool or type(self.news_conflict) is not bool:
            raise ValueError("invalid news flags")


@dataclass(frozen=True, slots=True)
class EventVerificationResult:
    event_id: str
    verified: bool
    reason: EventReason
    affected_symbols: tuple[str, ...]
    deadline_seconds: int


def verify_event(candidate: EventCandidate) -> EventVerificationResult:
    """Require two independent families and three ordered fresh samples per family."""
    if candidate.news_conflict:
        return _result(candidate, False, EventReason.DATA_CONFLICT)
    if candidate.kind is EventKind.NEWS:
        official_families = {
            SourceKind.FILING: SourceFamily.SEC,
            SourceKind.ISSUER_RELEASE: SourceFamily.ISSUER,
            SourceKind.EXCHANGE_NOTICE: SourceFamily.EXCHANGE,
        }
        official_family = (
            None
            if candidate.news_source_kind is None
            else official_families.get(candidate.news_source_kind)
        )
        if (
            candidate.news_primary
            and official_family is not None
            and candidate.news_source_families == (official_family,)
        ):
            return _result(candidate, True, EventReason.PRIMARY_OFFICIAL)
        if len(candidate.news_source_families) >= 2:
            return _result(candidate, True, EventReason.VERIFIED)
        return _result(candidate, False, EventReason.UNVERIFIED)

    observations = candidate.observations
    if any(item.symbol != candidate.symbol for item in observations):
        return _result(candidate, False, EventReason.DATA_CONFLICT)
    if any(item.observed_at.value > candidate.as_of.value for item in observations):
        return _result(candidate, False, EventReason.FUTURE)
    if any(item.fresh_until.value < candidate.as_of.value for item in observations):
        return _result(candidate, False, EventReason.STALE)
    families = {item.source_family for item in observations}
    if len(families) < 2:
        return _result(candidate, False, EventReason.SOURCE_FAMILY_COLLISION)
    grouped = {
        family: tuple(x for x in observations if x.source_family is family) for family in families
    }
    if any(len(samples) < 3 for samples in grouped.values()):
        return _result(candidate, False, EventReason.INSUFFICIENT_SAMPLES)
    for samples in grouped.values():
        times = [item.observed_at.value for item in samples]
        if any(current <= previous for previous, current in pairwise(times)):
            return _result(candidate, False, EventReason.OUT_OF_ORDER)
    latest_prices = [samples[-1].price for samples in grouped.values()]
    if max(latest_prices) / min(latest_prices) > Decimal("1.05"):
        return _result(candidate, False, EventReason.DATA_CONFLICT)
    return _result(candidate, True, EventReason.VERIFIED)


def _result(
    candidate: EventCandidate, verified: bool, reason: EventReason
) -> EventVerificationResult:
    return EventVerificationResult(candidate.event_id, verified, reason, (candidate.symbol,), 180)
