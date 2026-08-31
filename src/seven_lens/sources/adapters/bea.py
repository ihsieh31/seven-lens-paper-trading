"""BEA adapter: nested API envelopes with error propagation and period schema."""

from __future__ import annotations

import re
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    SourceSchemaDriftError,
    canonical_payload,
    content_hash_of,
    provider_utc_date,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.adapters.records import (
    _build_normalized_record as build_normalized_record,
)
from seven_lens.sources.roles import P4SourceFamily

_PERIOD: Final = re.compile(r"^Q[1-4]$|^A$")
_YEAR: Final = re.compile(r"^\d{4}$")
_DATASET: Final = re.compile(r"^[A-Za-z0-9]{1,40}$")
_TABLE: Final = re.compile(r"^[A-Za-z0-9-]{1,20}$")
_MAX_ROWS: Final = 10_000
_QUARTER_MONTH: Final = {"Q1": 1, "Q2": 4, "Q3": 7, "Q4": 10, "A": 1}


def parse_bea_data(
    payload: bytes, *, retrieved_at: UtcTimestamp, dataset: str, table_name: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one BEA API response; error envelopes fail closed."""
    if type(dataset) is not str or _DATASET.fullmatch(dataset) is None:
        raise SourceSchemaDriftError("dataset is not canonical")
    if type(table_name) is not str or _TABLE.fullmatch(table_name) is None:
        raise SourceSchemaDriftError("table name is not canonical")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("bea payload must be an object")
    require_keys(decoded, required={"BEAAPI"}, allowed={"BEAAPI"})
    envelope = require_type(decoded["BEAAPI"], dict, "BEAAPI")
    if type(envelope) is not dict:
        raise SourceSchemaDriftError("BEAAPI must be an object")
    if "Error" in envelope:
        raise SourceSchemaDriftError("bea response carried an error envelope")
    require_keys(envelope, required={"Results"}, allowed={"Results"})
    results = require_type(envelope["Results"], dict, "Results")
    if type(results) is not dict:
        raise SourceSchemaDriftError("Results must be an object")
    require_keys(results, required={"Data"}, allowed={"Data", "Statistic", "UnitOfMeasure"})
    data = require_type(results["Data"], list, "Data")
    if type(data) is not list or not data or len(data) > _MAX_ROWS:
        raise SourceSchemaDriftError("Data must be a non-empty bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for row in data:
        if not isinstance(row, dict):
            raise SourceSchemaDriftError("Data rows must be objects")
        require_keys(
            row,
            required={"Year", "Period", "DataValue"},
            allowed={"Year", "Period", "DataValue", "SeriesCode", "CL_UNIT", "UNIT_MULT"},
        )
        year = row["Year"]
        if type(year) is not str or _YEAR.fullmatch(year) is None:
            raise SourceSchemaDriftError("Data year must be YYYY")
        period = row["Period"]
        if type(period) is not str or _PERIOD.fullmatch(period) is None:
            raise SourceSchemaDriftError("Data period is unknown; fail closed")
        value = row["DataValue"]
        if type(value) is not str or not value:
            raise SourceSchemaDriftError("DataValue must be text")
        month = _QUARTER_MONTH[period]
        observation_at = provider_utc_date(f"{year}-{month:02d}-01")
        series_code = row.get("SeriesCode")
        if series_code is not None and type(series_code) is not str:
            raise SourceSchemaDriftError("SeriesCode must be text")
        records.append(
            build_normalized_record(
                record_id=(
                    f"bea-{dataset}-{table_name}-{series_code}-{year}-{period}-{content_hash[:12]}"
                ),
                family=P4SourceFamily.BEA,
                endpoint_id="bea_data",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=observation_at,
                payload=canonical_payload(
                    {
                        "dataset": dataset,
                        "table_name": table_name,
                        "year": year,
                        "period": period,
                        "value": value,
                        "series_code": series_code,
                    }
                ),
                material_claim=False,
            )
        )
    return tuple(records)
