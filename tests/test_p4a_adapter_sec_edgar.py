# mypy: ignore-errors
"""P4-A SEC EDGAR adapter tests: submissions schema, CIK identity, drift.

ADR-039 delta coverage: point-in-time SIC observation and the five exact
XBRL companyfacts concepts with accession closure and fail-closed semantics.
"""

from __future__ import annotations

import json

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.adapters.sec_edgar import (
    SEC_USER_AGENT,
    parse_companyfacts,
    parse_submissions,
)
from seven_lens.sources.contracts import RightsStatus
from seven_lens.sources.roles import P4SourceFamily, SourceRole, p4_manifest_registry

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_SUBMISSIONS_JSON = b"""{
  "cik": "320193",
  "entityType": "operating",
  "name": "Apple Inc.",
  "filings": {
    "recent": {
      "accessionNumber": ["0000320193-26-000001", "0000320193-26-000002"],
      "filingDate": ["2026-08-01", "2026-08-20"],
      "acceptanceDateTime": ["2026-08-01T18:04:22.000Z", "2026-08-20T17:31:00.000Z"],
      "form": ["10-Q", "8-K"],
      "primaryDocument": ["aapl-10q.htm", "aapl-8k.htm"]
    }
  }
}"""


def test_parse_submissions_builds_confirmation_records_with_cik_identity() -> None:
    records = parse_submissions(_SUBMISSIONS_JSON, retrieved_at=_RETRIEVED)

    assert len(records) == 2
    first = records[0]
    assert first.role is SourceRole.AUTHORITY
    assert first.rights is RightsStatus.ALLOWED
    payload = first.payload.to_dict()
    assert payload["cik_padded"] == "0000320193"
    assert payload["accession_number"] == "0000320193-26-000001"
    assert payload["form"] == "10-Q"
    assert first.record_id.startswith("sec-filing-")
    assert str(first.published_at) == "2026-08-01T18:04:22.000000Z"
    assert first.record_id != records[1].record_id


def test_sec_user_agent_is_an_identifiable_research_declaration() -> None:
    assert "seven-lens" in SEC_USER_AGENT
    assert "research" in SEC_USER_AGENT


def test_parse_submissions_rejects_mismatched_parallel_arrays() -> None:
    drifted = _SUBMISSIONS_JSON.replace(b'"10-Q", "8-K"', b'"10-Q"')
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(drifted, retrieved_at=_RETRIEVED)


def test_parse_submissions_rejects_bad_cik_or_accessions() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(
            _SUBMISSIONS_JSON.replace(b'"320193"', b'"32X193"'), retrieved_at=_RETRIEVED
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(
            _SUBMISSIONS_JSON.replace(b'"0000320193-26-000001"', b'"bad-accession"'),
            retrieved_at=_RETRIEVED,
        )


def test_parse_submissions_rejects_schema_drift() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(b"[]", retrieved_at=_RETRIEVED)
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(
            _SUBMISSIONS_JSON.replace(b'"10-Q", "8-K"', b'"10-Q", "8-K", "S-1"'),
            retrieved_at=_RETRIEVED,
        )


# ---------------------------------------------------------------------------
# ADR-039 delta: point-in-time SIC observation
# ---------------------------------------------------------------------------


def _submissions_with_sic(sic: str | None) -> bytes:
    payload = {
        "cik": "320193",
        "entityType": "operating",
        "name": "Apple Inc.",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000001"],
                "filingDate": ["2026-08-01"],
                "acceptanceDateTime": ["2026-08-01T18:04:22.000Z"],
                "form": ["10-Q"],
                "primaryDocument": ["aapl-10q.htm"],
            }
        },
    }
    if sic is not None:
        payload["sic"] = sic
    return json.dumps(payload).encode("utf-8")


def _sic_records(records) -> list:
    return [record for record in records if record.record_id.startswith("sec-sic-")]


@pytest.mark.parametrize("sic", ["0100", "1000", "3571"])
def test_parse_submissions_emits_point_in_time_sic_observation(sic: str) -> None:
    records = parse_submissions(_submissions_with_sic(sic), retrieved_at=_RETRIEVED)

    sic_only = _sic_records(records)
    assert len(sic_only) == 1
    observation = sic_only[0]
    payload = observation.payload.to_dict()
    assert payload["sic"] == sic
    assert payload["cik_padded"] == "0000320193"
    assert observation.record_id == "sec-sic-0000320193"
    assert observation.endpoint_id == "submissions"
    assert str(observation.observation_at) == str(_RETRIEVED)
    assert str(observation.available_at) == str(_RETRIEVED)
    assert observation.role is SourceRole.AUTHORITY
    # P4-A never maps SIC to GICS or sector; only the raw division code is kept.
    assert "sector" not in payload
    assert "gics" not in payload


def test_parse_submissions_zero_pads_short_sic_to_four_digits() -> None:
    records = parse_submissions(_submissions_with_sic("100"), retrieved_at=_RETRIEVED)

    sic_only = _sic_records(records)
    assert len(sic_only) == 1
    assert sic_only[0].payload.to_dict()["sic"] == "0100"


def test_parse_submissions_missing_sic_emits_no_sic_record_but_keeps_filings() -> None:
    records = parse_submissions(_submissions_with_sic(None), retrieved_at=_RETRIEVED)

    assert _sic_records(records) == []
    assert [record.record_id for record in records] == ["sec-filing-0000320193-26-000001"]


@pytest.mark.parametrize("sic", ["35X1", "12345", "", " 357", "35.7", "-100"])
def test_parse_submissions_rejects_malformed_sic(sic: str) -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(_submissions_with_sic(sic), retrieved_at=_RETRIEVED)


def test_sec_manifest_exposes_only_submissions_and_companyfacts_endpoints() -> None:
    policy = p4_manifest_registry().policy(P4SourceFamily.SEC_EDGAR)

    endpoint_ids = {endpoint.endpoint_id for endpoint in policy.endpoints}
    assert endpoint_ids == {"submissions", "companyfacts"}
    # No endpoint may let a caller supply an arbitrary taxonomy/concept path.
    for endpoint in policy.endpoints:
        assert "companyconcept" not in endpoint.path_template
        assert "{concept}" not in endpoint.path_template
        assert "{taxonomy}" not in endpoint.path_template


# ---------------------------------------------------------------------------
# ADR-039 delta: XBRL companyfacts five-concept normalization
# ---------------------------------------------------------------------------

_ACCEPTANCE = UtcTimestamp.from_isoformat("2026-08-01T18:04:22.000000Z")
_ACCESSION_A = "0000320193-26-000001"
_ACCESSION_B = "0000320193-26-000002"


def _fact(
    *,
    end: str = "2026-06-27",
    val: object = 23456000000,
    start: str | None = "2026-03-29",
    fy: int = 2026,
    fp: str = "Q3",
    form: str = "10-Q",
    filed: str = "2026-07-31",
    accn: str = _ACCESSION_A,
    unit: str | None = "USD",
) -> dict:
    fact = {"end": end, "val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "accn": accn}
    if start is not None:
        fact["start"] = start
    if unit is not None:
        fact["unit"] = unit
    return fact


def _companyfacts(facts: dict) -> bytes:
    return json.dumps({"cik": 320193, "entityName": "Apple Inc.", "facts": facts}).encode("utf-8")


def _five_concept_facts() -> dict:
    return {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [_fact(val=23456000000)]}},
            "NetCashProvidedByUsedInOperatingActivities": {
                "units": {"USD": [_fact(val=28000000000)]}
            },
            "Assets": {"units": {"USD": [_fact(val=350000000000, start=None)]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {
                "units": {"USD": [_fact(val=2500000000)]}
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [_fact(val=15552751000, start=None, unit="shares")]}
            }
        },
    }


def _acceptance(**extra) -> dict:
    mapping = {_ACCESSION_A: _ACCEPTANCE}
    mapping.update(extra)
    return mapping


def test_parse_companyfacts_normalizes_all_five_allowlisted_concepts() -> None:
    records = parse_companyfacts(
        _companyfacts(_five_concept_facts()),
        retrieved_at=_RETRIEVED,
        submission_acceptance=_acceptance(),
    )

    concepts = {(r.payload.to_dict()["taxonomy"], r.payload.to_dict()["concept"]) for r in records}
    assert concepts == {
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "Assets"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("dei", "EntityCommonStockSharesOutstanding"),
    }
    assert len(records) == 5
    for record in records:
        assert record.role is SourceRole.AUTHORITY
        assert record.endpoint_id == "companyfacts"
        assert record.family is P4SourceFamily.SEC_EDGAR
        assert str(record.available_at) == str(_ACCEPTANCE)


def test_parse_companyfacts_preserves_full_point_in_time_lineage() -> None:
    records = parse_companyfacts(
        _companyfacts(_five_concept_facts()),
        retrieved_at=_RETRIEVED,
        submission_acceptance=_acceptance(),
    )
    by_concept = {r.payload.to_dict()["concept"]: r for r in records}

    net_income = by_concept["NetIncomeLoss"]
    payload = net_income.payload.to_dict()
    assert payload["cik_padded"] == "0000320193"
    assert payload["unit"] == "USD"
    assert payload["value"] == "23456000000"
    assert payload["start"] == "2026-03-29"
    assert payload["end"] == "2026-06-27"
    assert payload["fiscal_year"] == 2026
    assert payload["fiscal_period"] == "Q3"
    assert payload["form"] == "10-Q"
    assert payload["accession"] == _ACCESSION_A
    assert payload["filed"] == "2026-07-31"
    assert str(net_income.observation_at) == "2026-06-27T00:00:00.000000Z"
    assert str(net_income.published_at) == "2026-07-31T00:00:00.000000Z"
    assert str(net_income.available_at) == str(_ACCEPTANCE)
    assert str(net_income.retrieved_at) == str(_RETRIEVED)
    assert net_income.content_hash
    assert str(net_income.schema_version) == "1.0.0"

    assets = by_concept["Assets"]
    assert assets.payload.to_dict()["start"] is None
    assert assets.payload.to_dict()["value"] == "350000000000"


def test_parse_companyfacts_capex_value_is_signed_and_never_abs() -> None:
    facts = _five_concept_facts()
    facts["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]["units"]["USD"] = [
        _fact(val=-2500000000)
    ]
    records = parse_companyfacts(
        _companyfacts(facts),
        retrieved_at=_RETRIEVED,
        submission_acceptance=_acceptance(),
    )
    capex = next(
        r
        for r in records
        if r.payload.to_dict()["concept"] == "PaymentsToAcquirePropertyPlantAndEquipment"
    )
    payload = capex.payload.to_dict()
    assert payload["value"] == "-2500000000"
    assert payload["sign_convention"] == "provider_value_preserved_no_abs"


def test_parse_companyfacts_rejects_unknown_extension_and_case_variant_concepts() -> None:
    facts = {
        "us-gaap": {
            "NetIncomeLossAbstract": {"units": {"USD": [_fact()]}},
            "netincomeloss": {"units": {"USD": [_fact()]}},
            "NetIncomeLossPerShare": {"units": {"USD": [_fact()]}},
        },
        "custom-taxonomy": {
            "NetIncomeLoss": {"units": {"USD": [_fact()]}},
        },
    }
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_submissions_rejects_invalid_or_unbound_filing_time() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(
            _SUBMISSIONS_JSON.replace(b'"2026-08-01"', b'"not-a-date"'),
            retrieved_at=_RETRIEVED,
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_submissions(
            _SUBMISSIONS_JSON.replace(b'"0000320193-26-000001"', b'"0000000001-26-000001"'),
            retrieved_at=_RETRIEVED,
        )


def test_parse_companyfacts_rejects_unit_field_conflicting_with_group() -> None:
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [_fact(unit="shares")]}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_duplicate_context_within_one_unit() -> None:
    facts = {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [_fact(val=1), _fact(val=2, accn=_ACCESSION_B)]}}
        }
    }
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(**{_ACCESSION_B: _ACCEPTANCE}),
        )


@pytest.mark.parametrize("bad_val", [True, False, 1.5, "23456000000", None, [1]])
def test_parse_companyfacts_rejects_non_integer_values(bad_val: object) -> None:
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [_fact(val=bad_val)]}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_nan_value_constant() -> None:
    payload = (
        b'{"cik":320193,"facts":{"us-gaap":{"NetIncomeLoss":{"units":{"USD":'
        b'[{"end":"2026-06-27","val":NaN,"fy":2026,"fp":"Q3","form":"10-Q",'
        b'"filed":"2026-07-31","accn":"0000320193-26-000001","start":"2026-03-29"}]}}}}'
    )
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(payload, retrieved_at=_RETRIEVED, submission_acceptance=_acceptance())


def test_parse_companyfacts_distinguishes_quarter_and_ytd_periods() -> None:
    quarter = _fact(start="2026-03-29", end="2026-06-27", fp="Q3", val=100)
    ytd = _fact(start="2025-09-28", end="2026-06-27", fp="Q3", val=300, accn=_ACCESSION_B)
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [quarter, ytd]}}}}
    records = parse_companyfacts(
        _companyfacts(facts),
        retrieved_at=_RETRIEVED,
        submission_acceptance=_acceptance(**{_ACCESSION_B: _ACCEPTANCE}),
    )
    starts = sorted(r.payload.to_dict()["start"] for r in records)
    assert starts == ["2025-09-28", "2026-03-29"]
    assert len(records) == 2


def test_parse_companyfacts_rejects_accession_not_closed_by_submissions() -> None:
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [_fact(accn="0000320193-26-000099")]}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_future_acceptance() -> None:
    future = UtcTimestamp.from_isoformat("2026-08-28T00:00:00.000000Z")
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [_fact()]}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance={_ACCESSION_A: future},
        )


def test_parse_companyfacts_rejects_period_end_after_retrieval() -> None:
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [_fact(end="2026-09-30")]}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_inverted_period() -> None:
    facts = {
        "us-gaap": {
            "NetIncomeLoss": {"units": {"USD": [_fact(start="2026-06-27", end="2026-03-29")]}}
        }
    }
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_oversize_payload_byte_budget() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            b"x" * 4_000_001,
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_rejects_unbounded_unit_fact_array() -> None:
    many = [_fact(end=f"2026-06-{(i % 27) + 1:02d}", accn=_ACCESSION_A) for i in range(1001)]
    facts = {"us-gaap": {"NetIncomeLoss": {"units": {"USD": many}}}}
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            _companyfacts(facts),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )


def test_parse_companyfacts_canonical_replay_is_byte_identical() -> None:
    payload = _companyfacts(_five_concept_facts())
    first = parse_companyfacts(
        payload, retrieved_at=_RETRIEVED, submission_acceptance=_acceptance()
    )
    second = parse_companyfacts(
        payload, retrieved_at=_RETRIEVED, submission_acceptance=_acceptance()
    )
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert [r.record_hash for r in first] == [r.record_hash for r in second]
    assert [r.content_hash for r in first] == [r.content_hash for r in second]
    for record in first:
        assert record.verify_integrity() is True


def test_parse_companyfacts_rejects_non_integer_cik_and_unknown_top_level_keys() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            json.dumps({"cik": "320193", "facts": {}}).encode("utf-8"),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_companyfacts(
            json.dumps({"cik": 320193, "facts": {}, "extra": 1}).encode("utf-8"),
            retrieved_at=_RETRIEVED,
            submission_acceptance=_acceptance(),
        )
