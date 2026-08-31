"""ADR-039 approved immutable manifests: factor, sector taxonomy, cluster.

Each manifest is frozen: its canonical wire form is the sole content
authority, and the domain-separated SHA-256 commitment over that wire is
pinned by golden tests.  Runtime, environment, model, and source inputs can
never override a manifest field.  P4-C implements only ``p4-factor-v1``,
``sec-sic-division-v1``, and ``p4-correlation-cluster-v1`` (the gross
turnover manifest is owned by P4-D).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Final

_FACTOR_HASH_DOMAIN: Final = b"seven-lens.p4c.manifest.factor-v1\x00"
_SECTOR_HASH_DOMAIN: Final = b"seven-lens.p4c.manifest.sector-v1\x00"
_CLUSTER_HASH_DOMAIN: Final = b"seven-lens.p4c.manifest.cluster-v1\x00"
_HASH_TEXT: Final = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_PRODUCER_VERSION: Final = "p4c.manifests.v1"
_MAX_MANIFEST_TEXT_BYTES: Final = 256
# SIC text is ASCII digits only: str.isdigit() would also accept Unicode
# decimal digits, and the approved taxonomy keys on ASCII "0"-"9" alone.
_SIC_TEXT: Final = re.compile(r"^[0-9]{3,4}$")


def _bounded_text(value: object, label: str) -> None:
    if type(value) is not str or not value:
        raise ValueError(f"{label} requires non-empty bounded text")
    encoded = value.encode("utf-8")
    if b"\x00" in encoded or len(encoded) > _MAX_MANIFEST_TEXT_BYTES:
        raise ValueError(f"{label} exceeds the {_MAX_MANIFEST_TEXT_BYTES}-byte text bound")


class FactorName(StrEnum):
    """The approved composite factor and its four categories."""

    COMPOSITE = "composite"
    TREND = "trend"
    QUALITY = "quality"
    VALUE = "value"
    LOW_RISK = "low_risk"


class RawSubfactor(StrEnum):
    """The nine raw subfactors; every one is mandatory in the cross-section."""

    TREND_126_21 = "trend_126_21"
    TREND_252_21 = "trend_252_21"
    ROA = "roa"
    CFO_TO_ASSETS = "cfo_to_assets"
    ACCRUAL_QUALITY = "accrual_quality"
    EARNINGS_YIELD = "earnings_yield"
    FCF_YIELD = "fcf_yield"
    VOL63 = "vol63"
    MAX_DRAWDOWN_252 = "max_drawdown_252"


class FundamentalConcept(StrEnum):
    """Exact SEC normalized fact concepts allowed by the manifest."""

    NET_INCOME_LOSS = "us-gaap:NetIncomeLoss"
    NET_CASH_OPERATING = "us-gaap:NetCashProvidedByUsedInOperatingActivities"
    ASSETS = "us-gaap:Assets"
    CAPEX_PPE = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
    SHARES_OUTSTANDING = "dei:EntityCommonStockSharesOutstanding"


@dataclass(frozen=True, slots=True)
class FactorManifest:
    """Frozen ``p4-factor-v1`` manifest with the approved formulas."""

    name: str
    producer_version: str
    weights: tuple[tuple[str, str], ...]
    subfactors: tuple[str, ...]
    concept_allowlist: tuple[str, ...]
    winsorize_low: str
    winsorize_high: str
    tie_break_order: tuple[str, ...]
    trend_lookbacks: tuple[str, ...]
    low_risk_sessions: str
    max_drawdown_sessions: str
    manifest_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name != "p4-factor-v1":
            raise ValueError("factor manifest name is pinned to p4-factor-v1")
        _bounded_text(self.producer_version, "producer_version")
        if self.producer_version != _MANIFEST_PRODUCER_VERSION:
            raise ValueError("factor manifest producer_version is not approved")
        if type(self.weights) is not tuple or self.weights != _FACTOR_WEIGHTS:
            raise ValueError("factor manifest weights are pinned")
        if type(self.subfactors) is not tuple or self.subfactors != _RAW_SUBFACTORS:
            raise ValueError("factor manifest subfactors are pinned")
        if type(self.concept_allowlist) is not tuple or self.concept_allowlist != _CONCEPTS:
            raise ValueError("factor manifest concept allowlist is pinned")
        if self.winsorize_low != "0.05" or self.winsorize_high != "0.95":
            raise ValueError("factor manifest winsorize bounds are pinned to 5%/95%")
        if type(self.tie_break_order) is not tuple or self.tie_break_order != _TIE_BREAK:
            raise ValueError("factor manifest tie-break order is pinned")
        if self.trend_lookbacks != ("126", "252"):
            raise ValueError("factor manifest trend lookbacks are pinned")
        if self.low_risk_sessions != "63" or self.max_drawdown_sessions != "252":
            raise ValueError("factor manifest risk windows are pinned")
        if type(self.manifest_hash) is not str or _HASH_TEXT.fullmatch(self.manifest_hash) is None:
            raise ValueError("manifest hash must be a SHA-256 digest")
        if self.manifest_hash != self.compute_hash():
            raise ValueError("manifest hash does not match frozen content")

    def wire(self) -> dict[str, object]:
        return {
            "name": self.name,
            "producer_version": self.producer_version,
            "weights": [list(pair) for pair in self.weights],
            "subfactors": list(self.subfactors),
            "concept_allowlist": list(self.concept_allowlist),
            "winsorize_low": self.winsorize_low,
            "winsorize_high": self.winsorize_high,
            "tie_break_order": list(self.tie_break_order),
            "trend_lookbacks": list(self.trend_lookbacks),
            "low_risk_sessions": self.low_risk_sessions,
            "max_drawdown_sessions": self.max_drawdown_sessions,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_FACTOR_HASH_DOMAIN + canonical).hexdigest()


_FACTOR_WEIGHTS: Final = (
    ("trend", "0.35"),
    ("quality", "0.25"),
    ("value", "0.15"),
    ("low_risk", "0.25"),
)
_RAW_SUBFACTORS: Final = tuple(item.value for item in RawSubfactor)
_CONCEPTS: Final = tuple(item.value for item in FundamentalConcept)
_TIE_BREAK: Final = ("composite", "trend", "quality", "value", "low_risk")


def factor_manifest(manifest_hash: str = "") -> FactorManifest:
    """Return the single approved factor manifest; hash may only pin it."""
    if manifest_hash:
        return FactorManifest(
            name="p4-factor-v1",
            producer_version="p4c.manifests.v1",
            weights=_FACTOR_WEIGHTS,
            subfactors=_RAW_SUBFACTORS,
            concept_allowlist=_CONCEPTS,
            winsorize_low="0.05",
            winsorize_high="0.95",
            tie_break_order=_TIE_BREAK,
            trend_lookbacks=("126", "252"),
            low_risk_sessions="63",
            max_drawdown_sessions="252",
            manifest_hash=manifest_hash,
        )
    body: dict[str, object] = {
        "name": "p4-factor-v1",
        "producer_version": "p4c.manifests.v1",
        "weights": _FACTOR_WEIGHTS,
        "subfactors": _RAW_SUBFACTORS,
        "concept_allowlist": _CONCEPTS,
        "winsorize_low": "0.05",
        "winsorize_high": "0.95",
        "tie_break_order": _TIE_BREAK,
        "trend_lookbacks": ("126", "252"),
        "low_risk_sessions": "63",
        "max_drawdown_sessions": "252",
    }
    provisional = object.__new__(FactorManifest)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_hash", "")
    computed = provisional.compute_hash()
    body["manifest_hash"] = computed
    return FactorManifest(**body)  # type: ignore[arg-type]


class SicDivision(StrEnum):
    """SEC SIC Division taxonomy with the approved one-letter divisions."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"  # noqa: E741
    J = "J"
    SECTOR_UNKNOWN = "SECTOR_UNKNOWN"


_SIC_RANGES: Final[tuple[tuple[tuple[int, int], SicDivision], ...]] = (
    ((1, 9), SicDivision.A),
    ((10, 14), SicDivision.B),
    ((15, 17), SicDivision.C),
    ((20, 39), SicDivision.D),
    ((40, 49), SicDivision.E),
    ((50, 51), SicDivision.F),
    ((52, 59), SicDivision.G),
    ((60, 67), SicDivision.H),
    ((70, 89), SicDivision.I),
    ((91, 97), SicDivision.J),
)


@dataclass(frozen=True, slots=True)
class SectorManifest:
    """Frozen ``sec-sic-division-v1`` taxonomy manifest."""

    name: str
    producer_version: str
    ranges: tuple[tuple[str, str, str], ...]
    unknown_ranges: tuple[str, ...]
    manifest_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name != "sec-sic-division-v1":
            raise ValueError("sector manifest name is pinned to sec-sic-division-v1")
        _bounded_text(self.producer_version, "producer_version")
        if self.producer_version != _MANIFEST_PRODUCER_VERSION:
            raise ValueError("sector manifest producer_version is not approved")
        if type(self.ranges) is not tuple or self.ranges != _SIC_WIRE_RANGES:
            raise ValueError("sector manifest ranges are pinned")
        if self.unknown_ranges != ("18-19", "68-69", "90", "98", "99"):
            raise ValueError("sector manifest unknown ranges are pinned")
        if type(self.manifest_hash) is not str or _HASH_TEXT.fullmatch(self.manifest_hash) is None:
            raise ValueError("manifest hash must be a SHA-256 digest")
        if self.manifest_hash != self.compute_hash():
            raise ValueError("manifest hash does not match frozen content")

    def wire(self) -> dict[str, object]:
        return {
            "name": self.name,
            "producer_version": self.producer_version,
            "ranges": [list(r) for r in self.ranges],
            "unknown_ranges": list(self.unknown_ranges),
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_SECTOR_HASH_DOMAIN + canonical).hexdigest()


_SIC_WIRE_RANGES: Final = tuple(
    (f"{low:02d}-{high:02d}", f"{low:02d}", division.value) for (low, high), division in _SIC_RANGES
)


def sector_manifest(manifest_hash: str = "") -> SectorManifest:
    """Return the single approved sector taxonomy manifest."""
    if manifest_hash:
        return SectorManifest(
            name="sec-sic-division-v1",
            producer_version="p4c.manifests.v1",
            ranges=_SIC_WIRE_RANGES,
            unknown_ranges=("18-19", "68-69", "90", "98", "99"),
            manifest_hash=manifest_hash,
        )
    body: dict[str, object] = {
        "name": "sec-sic-division-v1",
        "producer_version": "p4c.manifests.v1",
        "ranges": _SIC_WIRE_RANGES,
        "unknown_ranges": ("18-19", "68-69", "90", "98", "99"),
    }
    provisional = object.__new__(SectorManifest)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_hash", "")
    computed = provisional.compute_hash()
    body["manifest_hash"] = computed
    return SectorManifest(**body)  # type: ignore[arg-type]


def classify_sic(sic: str | None) -> SicDivision:
    """Classify one exact 4-digit SIC string under the approved taxonomy.

    Classification uses the first two digits of the SIC (major group).
    Only an exact 4-digit zero-padded string of ASCII digits is accepted; a
    3-digit value is left-padded with zero.  Any other shape (including
    Unicode digit characters), missing value, or gap range returns
    ``SECTOR_UNKNOWN``.  This function never looks up a fallback taxonomy and
    never returns a label outside the approved SIC divisions.
    """
    if type(sic) is not str or _SIC_TEXT.fullmatch(sic) is None:
        return SicDivision.SECTOR_UNKNOWN
    digits = sic.zfill(4)
    try:
        prefix = int(digits[:2])
    except ValueError:
        return SicDivision.SECTOR_UNKNOWN
    for (low, high), division in _SIC_RANGES:
        if low <= prefix <= high:
            return division
    return SicDivision.SECTOR_UNKNOWN


class ClusterStatus(StrEnum):
    """Closed cluster statuses; UNKNOWN can never degrade to a singleton."""

    ASSIGNED = "ASSIGNED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ClusterManifest:
    """Frozen ``p4-correlation-cluster-v1`` manifest."""

    name: str
    producer_version: str
    sessions: str
    min_returns: str
    min_pair_observations: str
    correlation_threshold: str
    method: str
    manifest_hash: str

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name != "p4-correlation-cluster-v1":
            raise ValueError("cluster manifest name is pinned to p4-correlation-cluster-v1")
        _bounded_text(self.producer_version, "producer_version")
        if self.producer_version != _MANIFEST_PRODUCER_VERSION:
            raise ValueError("cluster manifest producer_version is not approved")
        if self.sessions != "126":
            raise ValueError("cluster manifest session window is pinned to 126")
        if self.min_returns != "100":
            raise ValueError("cluster manifest minimum returns is pinned to 100")
        if self.min_pair_observations != "100":
            raise ValueError("cluster manifest minimum pair observations is pinned to 100")
        if self.correlation_threshold != "0.75":
            raise ValueError("cluster manifest correlation threshold is pinned to 0.75")
        if self.method != "pearson":
            raise ValueError("cluster manifest method is pinned to Pearson")
        if type(self.manifest_hash) is not str or _HASH_TEXT.fullmatch(self.manifest_hash) is None:
            raise ValueError("manifest hash must be a SHA-256 digest")
        if self.manifest_hash != self.compute_hash():
            raise ValueError("manifest hash does not match frozen content")

    def wire(self) -> dict[str, object]:
        return {
            "name": self.name,
            "producer_version": self.producer_version,
            "sessions": self.sessions,
            "min_returns": self.min_returns,
            "min_pair_observations": self.min_pair_observations,
            "correlation_threshold": self.correlation_threshold,
            "method": self.method,
        }

    def compute_hash(self) -> str:
        canonical = json.dumps(
            self.wire(), allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return sha256(_CLUSTER_HASH_DOMAIN + canonical).hexdigest()


def cluster_manifest(manifest_hash: str = "") -> ClusterManifest:
    """Return the single approved correlation-cluster manifest."""
    if manifest_hash:
        return ClusterManifest(
            name="p4-correlation-cluster-v1",
            producer_version="p4c.manifests.v1",
            sessions="126",
            min_returns="100",
            min_pair_observations="100",
            correlation_threshold="0.75",
            method="pearson",
            manifest_hash=manifest_hash,
        )
    body: dict[str, object] = {
        "name": "p4-correlation-cluster-v1",
        "producer_version": "p4c.manifests.v1",
        "sessions": "126",
        "min_returns": "100",
        "min_pair_observations": "100",
        "correlation_threshold": "0.75",
        "method": "pearson",
    }
    provisional = object.__new__(ClusterManifest)
    for name, value in body.items():
        object.__setattr__(provisional, name, value)
    object.__setattr__(provisional, "manifest_hash", "")
    computed = provisional.compute_hash()
    body["manifest_hash"] = computed
    return ClusterManifest(**body)  # type: ignore[arg-type]


def decimal_bps(value: str) -> Decimal:
    """Parse a pinned decimal text into an exact Decimal."""
    if type(value) is not str:
        raise ValueError("manifest decimal input must be text")
    try:
        parsed = Decimal(value)
    except Exception as error:
        raise ValueError("manifest decimal text is invalid") from error
    if not parsed.is_finite():
        raise ValueError("manifest decimal must be finite")
    return parsed
