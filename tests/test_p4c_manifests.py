"""P4-C ADR-039 manifest golden hashes and SIC taxonomy tests."""

from __future__ import annotations

import pytest

from seven_lens.screening.manifests import (
    SicDivision,
    classify_sic,
    cluster_manifest,
    factor_manifest,
    sector_manifest,
)

# Golden hashes pin the exact canonical wire of each approved manifest.  Any
# formula, weight, threshold, taxonomy, or wire-format change breaks these.
_FACTOR_GOLDEN = "a95be51a7468c73a8a6bfdda05fb4fd9076703afdae9f3a9bff7b2d4a8f6fcc7"
_SECTOR_GOLDEN = "816dad7c0d8daa45dcb0fef0b18b27552f5f471fbb7ab725328bd9562b1e2136"
_CLUSTER_GOLDEN = "34aa2e2e2056cb21495ed398ab2d816ee90b9fd257c632a878466989ef3cfa0e"


def test_factor_manifest_golden_hash() -> None:
    manifest = factor_manifest()
    assert manifest.name == "p4-factor-v1"
    assert manifest.manifest_hash == _FACTOR_GOLDEN
    assert factor_manifest(manifest_hash=_FACTOR_GOLDEN).manifest_hash == _FACTOR_GOLDEN


def test_factor_manifest_pins_weights_and_tie_break() -> None:
    manifest = factor_manifest()
    assert manifest.weights == (
        ("trend", "0.35"),
        ("quality", "0.25"),
        ("value", "0.15"),
        ("low_risk", "0.25"),
    )
    assert manifest.tie_break_order == (
        "composite",
        "trend",
        "quality",
        "value",
        "low_risk",
    )
    assert manifest.winsorize_low == "0.05"
    assert manifest.winsorize_high == "0.95"


def test_factor_manifest_pins_subfactors_and_concepts() -> None:
    manifest = factor_manifest()
    assert len(manifest.subfactors) == 9
    assert "trend_126_21" in manifest.subfactors
    assert "trend_252_21" in manifest.subfactors
    assert "roa" in manifest.subfactors
    assert "cfo_to_assets" in manifest.subfactors
    assert "accrual_quality" in manifest.subfactors
    assert "earnings_yield" in manifest.subfactors
    assert "fcf_yield" in manifest.subfactors
    assert "vol63" in manifest.subfactors
    assert "max_drawdown_252" in manifest.subfactors
    assert "us-gaap:NetIncomeLoss" in manifest.concept_allowlist
    assert "us-gaap:NetCashProvidedByUsedInOperatingActivities" in manifest.concept_allowlist
    assert "us-gaap:Assets" in manifest.concept_allowlist
    assert "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment" in manifest.concept_allowlist
    assert "dei:EntityCommonStockSharesOutstanding" in manifest.concept_allowlist


def test_factor_manifest_rejects_hash_drift() -> None:
    with pytest.raises(ValueError):
        factor_manifest(manifest_hash="0" * 64)


def test_sector_manifest_golden_hash() -> None:
    manifest = sector_manifest()
    assert manifest.name == "sec-sic-division-v1"
    assert manifest.manifest_hash == _SECTOR_GOLDEN
    assert sector_manifest(manifest_hash=_SECTOR_GOLDEN).manifest_hash == _SECTOR_GOLDEN


def test_sector_manifest_pins_unknown_ranges() -> None:
    manifest = sector_manifest()
    assert manifest.unknown_ranges == ("18-19", "68-69", "90", "98", "99")


def test_cluster_manifest_golden_hash() -> None:
    manifest = cluster_manifest()
    assert manifest.name == "p4-correlation-cluster-v1"
    assert manifest.manifest_hash == _CLUSTER_GOLDEN
    assert cluster_manifest(manifest_hash=_CLUSTER_GOLDEN).manifest_hash == _CLUSTER_GOLDEN


def test_cluster_manifest_pins_parameters() -> None:
    manifest = cluster_manifest()
    assert manifest.sessions == "126"
    assert manifest.min_returns == "100"
    assert manifest.min_pair_observations == "100"
    assert manifest.correlation_threshold == "0.75"
    assert manifest.method == "pearson"


@pytest.mark.parametrize(
    ("sic", "expected"),
    [
        ("0110", SicDivision.A),
        ("0911", SicDivision.A),
        ("1010", SicDivision.B),
        ("1410", SicDivision.B),
        ("1510", SicDivision.C),
        ("1710", SicDivision.C),
        ("2010", SicDivision.D),
        ("3990", SicDivision.D),
        ("4010", SicDivision.E),
        ("4910", SicDivision.E),
        ("5010", SicDivision.F),
        ("5110", SicDivision.F),
        ("5210", SicDivision.G),
        ("5990", SicDivision.G),
        ("6010", SicDivision.H),
        ("6710", SicDivision.H),
        ("7010", SicDivision.I),
        ("8910", SicDivision.I),
        ("9110", SicDivision.J),
        ("9710", SicDivision.J),
        ("1899", SicDivision.SECTOR_UNKNOWN),
        ("6899", SicDivision.SECTOR_UNKNOWN),
        ("9000", SicDivision.SECTOR_UNKNOWN),
        ("9800", SicDivision.SECTOR_UNKNOWN),
        ("9900", SicDivision.SECTOR_UNKNOWN),
    ],
)
def test_sic_classification_boundaries(sic: str, expected: SicDivision) -> None:
    assert classify_sic(sic) is expected


def test_sic_three_digit_zero_pad() -> None:
    # A 3-digit SIC is left-padded to 4 digits before the first-2 mapping.
    # "371" → "0371" → 03 → A (Agriculture)
    assert classify_sic("371") is SicDivision.A
    # "104" → "0104" → 01 → A
    assert classify_sic("104") is SicDivision.A
    # "410" → "0410" → 04 → A (Agriculture)
    assert classify_sic("410") is SicDivision.A


@pytest.mark.parametrize(
    "bad",
    [None, "", "20", "12345", "abcd", "12.5", "-100", " 100", "100 "],
)
def test_sic_rejects_malformed_values(bad: object) -> None:
    assert classify_sic(bad) is SicDivision.SECTOR_UNKNOWN  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "unicode_digits",
    # Arabic-Indic digits, fullwidth digits, superscript digits.
    ["\u0666\u0661\u0667\u0660", "\uff16\uff11\uff17\uff10", "\u00b2\u00b3\u2074", "\u0666\u0661"],
)
def test_sic_rejects_unicode_digit_characters(unicode_digits: str) -> None:
    # str.isdigit() accepts Unicode decimal digits; the approved taxonomy keys
    # on ASCII "0"-"9" alone, so every non-ASCII shape is SECTOR_UNKNOWN.
    assert classify_sic(unicode_digits) is SicDivision.SECTOR_UNKNOWN


def test_sic_uses_only_the_approved_taxonomy_manifest() -> None:
    name = sector_manifest().wire()["name"]
    assert name == "sec-sic-division-v1"
    for division in SicDivision:
        assert division.value in {d.value for d in SicDivision}
