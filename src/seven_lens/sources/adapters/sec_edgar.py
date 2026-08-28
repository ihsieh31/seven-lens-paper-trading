"""SEC EDGAR adapter: submissions identity/SIC plus ADR-039 XBRL companyfacts.

P4-A scope is normalization and point-in-time lineage only.  This module never
computes TTM, quarter decomposition, market cap, factors, SIC Division mapping,
or Risk; those belong to later gates.  Every XBRL fact must close against an
accepted submission accession before an available time may be asserted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    SourceSchemaDriftError,
    build_normalized_record,
    canonical_payload,
    content_hash_of,
    parse_provider_timestamp,
    require_date,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.roles import P4SourceFamily

SEC_USER_AGENT = (
    "seven-lens-paper-trading/0.1 (research-use; paper-only; contact registered at acceptance)"
)
_CIK: Final = re.compile(r"^[0-9]{1,10}$")
_ACCESSION: Final = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")
_FORM: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,19}$")
_FORM_FACT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/-]{0,19}$")
_SIC: Final = re.compile(r"^[0-9]{1,4}$")
_FISCAL_PERIOD: Final = re.compile(r"^(?:Q[1-4]|FY)$")
_MAX_RECENT: Final = 1_000
_MAX_FACTS_PER_UNIT: Final = 1_000
_FACT_ID_DOMAIN: Final = b"seven-lens.p4.sec-companyfact-id.v1\x00"

# ADR-039: the only (taxonomy, concept) pairs P4-A may normalize.  Matching is
# exact; no suffix, case-fold, extension, or first-match substitution is allowed.
_CONCEPT_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("us-gaap", "Assets"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("dei", "EntityCommonStockSharesOutstanding"),
    }
)
_CAPEX_CONCEPT: Final = "PaymentsToAcquirePropertyPlantAndEquipment"
_CAPEX_SIGN_CONVENTION: Final = "provider_value_preserved_no_abs"


def parse_submissions(
    payload: bytes, *, retrieved_at: UtcTimestamp
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate the EDGAR submissions JSON and emit filing plus SIC records.

    Emits one point-in-time SIC observation record when a valid top-level SIC is
    present, then one record per recent filing.  A missing SIC yields no SIC
    observation (the entity's SIC stays unknown; it is never guessed); a present
    but malformed SIC is a typed schema-drift failure.
    """
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("submissions payload must be an object")
    require_keys(
        decoded,
        required={"cik", "filings"},
        allowed={"cik", "entityType", "sic", "name", "filings"},
    )
    cik = require_type(decoded["cik"], str, "cik")
    if type(cik) is not str or _CIK.fullmatch(cik) is None:
        raise SourceSchemaDriftError("cik must be numeric text")
    cik_padded = cik.zfill(10)
    content_hash = content_hash_of(payload)

    sic_records: list[NormalizedSourceRecord] = []
    if "sic" in decoded:
        sic_raw = decoded["sic"]
        if type(sic_raw) is not str or _SIC.fullmatch(sic_raw) is None:
            raise SourceSchemaDriftError("top-level sic must be 1-4 digit numeric text")
        sic_padded = sic_raw.zfill(4)
        sic_records.append(
            build_normalized_record(
                record_id=f"sec-sic-{cik_padded}",
                family=P4SourceFamily.SEC_EDGAR,
                endpoint_id="submissions",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=retrieved_at,
                available_at=retrieved_at,
                payload=canonical_payload({"cik_padded": cik_padded, "sic": sic_padded}),
                material_claim=False,
            )
        )

    filings = require_type(decoded["filings"], dict, "filings")
    if type(filings) is not dict or "recent" not in filings:
        raise SourceSchemaDriftError("filings.recent is missing")
    recent = filings["recent"]
    if not isinstance(recent, dict):
        raise SourceSchemaDriftError("filings.recent must be an object")
    require_keys(
        recent,
        required={"accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument"},
        allowed={
            "accessionNumber",
            "filingDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
        },
    )
    columns = {
        name: recent[name]
        for name in (
            "accessionNumber",
            "filingDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
        )
    }
    lengths = set()
    for name, column in columns.items():
        if not isinstance(column, list):
            raise SourceSchemaDriftError(f"filings.recent.{name} must be an array")
        lengths.add(len(column))
        if len(column) > _MAX_RECENT:
            raise SourceSchemaDriftError("filings.recent arrays exceed the bound")
    if len(lengths) != 1 or 0 in lengths:
        raise SourceSchemaDriftError("filings.recent parallel arrays must be equally sized")
    accessions = columns["accessionNumber"]
    if len({str(item) for item in accessions}) != len(accessions):
        raise SourceSchemaDriftError("filings.recent accessions must be unique")
    records: list[NormalizedSourceRecord] = []
    for index, accession in enumerate(accessions):
        if type(accession) is not str or _ACCESSION.fullmatch(accession) is None:
            raise SourceSchemaDriftError("accession number is not canonical")
        form = columns["form"][index]
        if type(form) is not str or _FORM.fullmatch(form) is None:
            raise SourceSchemaDriftError("form type is not canonical")
        primary = columns["primaryDocument"][index]
        if type(primary) is not str or not primary:
            raise SourceSchemaDriftError("primary document must be text")
        filing_date = columns["filingDate"][index]
        if type(filing_date) is not str:
            raise SourceSchemaDriftError("filing date must be text")
        filing_date_ts = require_date(filing_date, "filing date")
        acceptance = columns["acceptanceDateTime"][index]
        if type(acceptance) is not str:
            raise SourceSchemaDriftError("acceptance time must be text")
        try:
            published_at = parse_provider_timestamp(acceptance)
        except Exception as error:
            raise SourceSchemaDriftError("acceptance time is not canonical") from error
        if filing_date_ts.value > retrieved_at.value:
            raise SourceSchemaDriftError("filing date is after retrieval")
        if published_at.value < filing_date_ts.value:
            raise SourceSchemaDriftError("acceptance time precedes filing date")
        if accession[:10] != cik_padded:
            raise SourceSchemaDriftError("filing accession does not match the submission cik")
        records.append(
            build_normalized_record(
                record_id=f"sec-filing-{accession}",
                family=P4SourceFamily.SEC_EDGAR,
                endpoint_id="submissions",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                published_at=published_at,
                payload=canonical_payload(
                    {
                        "cik_padded": cik_padded,
                        "accession_number": accession,
                        "form": form,
                        "primary_document": primary,
                        "filing_date": filing_date,
                    }
                ),
                material_claim=False,
            )
        )
    return (*sic_records, *records)


def _fact_record_id(
    *,
    cik_padded: str,
    taxonomy: str,
    concept: str,
    unit: str,
    start: str | None,
    end: str,
    accession: str,
) -> str:
    """Derive a deterministic, content-bound record id for one XBRL fact."""
    identity = json.dumps(
        {
            "cik": cik_padded,
            "taxonomy": taxonomy,
            "concept": concept,
            "unit": unit,
            "start": start,
            "end": end,
            "accession": accession,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(_FACT_ID_DOMAIN + identity).hexdigest()
    return f"sec-fact-{concept}-{digest[:16]}"


def parse_companyfacts(
    payload: bytes,
    *,
    retrieved_at: UtcTimestamp,
    submission_acceptance: Mapping[str, UtcTimestamp],
) -> tuple[NormalizedSourceRecord, ...]:
    """Normalize allowlisted XBRL companyfacts into point-in-time records.

    Only the exact ADR-039 ``(taxonomy, concept)`` allowlist is accepted; any
    other taxonomy or concept is rejected (never matched by suffix, case-fold,
    extension, or first-match).  Every fact must close its accession against an
    accepted submission to obtain an available time; an unjoined accession, a
    conflicting unit/context, a non-integer value, or a future availability is a
    typed fail-closed error.  Values are preserved exactly as signed Decimals and
    are never ``abs()``-ed.
    """
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("companyfacts payload must be an object")
    require_keys(
        decoded,
        required={"cik", "facts"},
        allowed={"cik", "entityName", "facts"},
    )
    cik = decoded["cik"]
    if type(cik) is not int or not 0 < cik < 10_000_000_000:
        raise SourceSchemaDriftError("companyfacts cik must be a bounded positive integer")
    cik_padded = str(cik).zfill(10)
    facts = require_type(decoded["facts"], dict, "facts")
    if type(facts) is not dict:
        raise SourceSchemaDriftError("companyfacts facts must be an object")
    for taxonomy, taxonomy_node in facts.items():
        if type(taxonomy) is not str or (taxonomy, "") not in {
            (allowed_taxonomy, "") for allowed_taxonomy, _ in _CONCEPT_ALLOWLIST
        }:
            raise SourceSchemaDriftError("companyfacts carries an unknown taxonomy")
        if type(taxonomy_node) is not dict:
            raise SourceSchemaDriftError(f"facts.{taxonomy} must be an object")
        for concept in taxonomy_node:
            if (taxonomy, concept) not in _CONCEPT_ALLOWLIST:
                raise SourceSchemaDriftError(
                    f"companyfacts carries a non-allowlisted concept: {taxonomy}.{concept}"
                )
    content_hash = content_hash_of(payload)

    records: list[NormalizedSourceRecord] = []
    for taxonomy, concept in sorted(_CONCEPT_ALLOWLIST):
        taxonomy_node = facts.get(taxonomy)
        if taxonomy_node is None:
            continue
        if not isinstance(taxonomy_node, dict):
            raise SourceSchemaDriftError(f"facts.{taxonomy} must be an object")
        concept_node = taxonomy_node.get(concept)
        if concept_node is None:
            continue
        if not isinstance(concept_node, dict):
            raise SourceSchemaDriftError(f"facts.{taxonomy}.{concept} must be an object")
        require_keys(
            concept_node,
            required={"units"},
            allowed={"units", "label", "description"},
        )
        units = require_type(concept_node["units"], dict, "units")
        if type(units) is not dict or not units:
            raise SourceSchemaDriftError("concept units must be a non-empty object")
        for unit_key in units:
            if type(unit_key) is not str or not unit_key:
                raise SourceSchemaDriftError("unit key must be non-empty text")
            facts_array = units[unit_key]
            if type(facts_array) is not list:
                raise SourceSchemaDriftError("unit facts must be an array")
            if len(facts_array) > _MAX_FACTS_PER_UNIT:
                raise SourceSchemaDriftError("unit facts exceed the per-unit bound")
            seen_contexts: set[tuple[str | None, str]] = set()
            for fact in facts_array:
                records.append(
                    _normalize_fact(
                        fact,
                        cik_padded=cik_padded,
                        taxonomy=taxonomy,
                        concept=concept,
                        unit_key=unit_key,
                        content_hash=content_hash,
                        retrieved_at=retrieved_at,
                        submission_acceptance=submission_acceptance,
                        seen_contexts=seen_contexts,
                    )
                )
    return tuple(records)


def _normalize_fact(
    fact: object,
    *,
    cik_padded: str,
    taxonomy: str,
    concept: str,
    unit_key: str,
    content_hash: str,
    retrieved_at: UtcTimestamp,
    submission_acceptance: Mapping[str, UtcTimestamp],
    seen_contexts: set[tuple[str | None, str]],
) -> NormalizedSourceRecord:
    """Validate one XBRL fact and build its point-in-time normalized record."""
    if not isinstance(fact, dict):
        raise SourceSchemaDriftError("fact entries must be objects")
    require_keys(
        fact,
        required={"end", "val", "fy", "fp", "form", "filed", "accn"},
        allowed={
            "end",
            "val",
            "fy",
            "fp",
            "form",
            "filed",
            "accn",
            "start",
            "unit",
            "frame",
            "avg",
        },
    )
    if "unit" in fact:
        unit_field = fact["unit"]
        if type(unit_field) is not str or unit_field != unit_key:
            raise SourceSchemaDriftError("fact unit conflicts with its unit group")
    for optional_text in ("frame", "avg"):
        if optional_text in fact and type(fact[optional_text]) is not str:
            raise SourceSchemaDriftError(f"{optional_text} must be text")

    value = fact["val"]
    if type(value) is not int:
        raise SourceSchemaDriftError("fact value must be an exact integer (no bool/float/NaN)")
    value_decimal = Decimal(value)

    end_text = fact["end"]
    end_ts = require_date(end_text, "end")
    start_text: str | None = None
    start_ts: UtcTimestamp | None = None
    if "start" in fact:
        start_text = fact["start"]
        start_ts = require_date(start_text, "start")
        if start_ts.value >= end_ts.value:
            raise SourceSchemaDriftError("fact period start must precede its end")
    filed_text = fact["filed"]
    filed_ts = require_date(filed_text, "filed")

    fiscal_year = fact["fy"]
    if type(fiscal_year) is not int or not 1000 <= fiscal_year <= 9999:
        raise SourceSchemaDriftError("fiscal year must be a bounded integer")
    fiscal_period = fact["fp"]
    if type(fiscal_period) is not str or _FISCAL_PERIOD.fullmatch(fiscal_period) is None:
        raise SourceSchemaDriftError("fiscal period must be Q1..Q4 or FY")
    form = fact["form"]
    if type(form) is not str or _FORM_FACT.fullmatch(form) is None:
        raise SourceSchemaDriftError("fact form is not canonical")
    accession = fact["accn"]
    if type(accession) is not str or _ACCESSION.fullmatch(accession) is None:
        raise SourceSchemaDriftError("fact accession number is not canonical")
    if accession[:10] != cik_padded:
        raise SourceSchemaDriftError("fact accession does not match the companyfacts cik")

    context_key = (start_text, end_text)
    if context_key in seen_contexts:
        raise SourceSchemaDriftError("duplicate fact context within one concept unit")
    seen_contexts.add(context_key)

    acceptance = submission_acceptance.get(accession)
    if acceptance is None:
        raise SourceSchemaDriftError("fact accession does not close against an accepted submission")
    if type(acceptance) is not UtcTimestamp:
        raise SourceSchemaDriftError("submission acceptance must be a canonical UTC timestamp")
    if acceptance.value > retrieved_at.value:
        raise SourceSchemaDriftError("fact acceptance is after retrieval (future availability)")
    if end_ts.value > retrieved_at.value:
        raise SourceSchemaDriftError("fact period end is after retrieval")
    if filed_ts.value > retrieved_at.value:
        raise SourceSchemaDriftError("fact filed date is after retrieval")

    payload: dict[str, object] = {
        "cik_padded": cik_padded,
        "taxonomy": taxonomy,
        "concept": concept,
        "unit": unit_key,
        "value": str(value_decimal),
        "start": start_text,
        "end": end_text,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "form": form,
        "accession": accession,
        "filed": filed_text,
    }
    if concept == _CAPEX_CONCEPT:
        payload["sign_convention"] = _CAPEX_SIGN_CONVENTION

    return build_normalized_record(
        record_id=_fact_record_id(
            cik_padded=cik_padded,
            taxonomy=taxonomy,
            concept=concept,
            unit=unit_key,
            start=start_text,
            end=end_text,
            accession=accession,
        ),
        family=P4SourceFamily.SEC_EDGAR,
        endpoint_id="companyfacts",
        schema_version=schema_version("1.0.0"),
        content_hash=content_hash,
        retrieved_at=retrieved_at,
        observation_at=end_ts,
        published_at=filed_ts,
        available_at=acceptance,
        payload=canonical_payload(payload),
        material_claim=False,
    )
