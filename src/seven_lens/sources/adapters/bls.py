"""BLS adapter: v2 GET series payloads with strict status and period schemas."""

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

_PERIOD: Final = re.compile(r"^M(0[1-9]|1[0-3])$|^Q0[1-5]$|^A01$")
_YEAR: Final = re.compile(r"^\d{4}$")
_SERIES_ID: Final = re.compile(r"^[A-Z0-9]{1,20}$")
_SUCCESS_STATUS: Final = "REQUEST_SUCCEEDED"
_MAX_SERIES: Final = 50
_MAX_OBSERVATIONS_PER_SERIES: Final = 2_000
_MONTH_OF_PERIOD: Final = {
    "M01": 1,
    "M02": 2,
    "M03": 3,
    "M04": 4,
    "M05": 5,
    "M06": 6,
    "M07": 7,
    "M08": 8,
    "M09": 9,
    "M10": 10,
    "M11": 11,
    "M12": 12,
    "M13": 12,
}


def parse_series(
    payload: bytes, *, retrieved_at: UtcTimestamp
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one BLS GET response; failed request statuses fail closed."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("bls payload must be an object")
    require_keys(
        decoded,
        required={"status", "Results"},
        allowed={"status", "responseTime", "message", "Results"},
    )
    if decoded["status"] != _SUCCESS_STATUS:
        raise SourceSchemaDriftError("bls request status did not succeed")
    results = require_type(decoded["Results"], dict, "Results")
    if type(results) is not dict:
        raise SourceSchemaDriftError("Results must be an object")
    require_keys(results, required={"series"}, allowed={"series", "notes"})
    series_list = require_type(results["series"], list, "series")
    if type(series_list) is not list or not series_list or len(series_list) > _MAX_SERIES:
        raise SourceSchemaDriftError("series must be a non-empty bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for series in series_list:
        if not isinstance(series, dict):
            raise SourceSchemaDriftError("series entries must be objects")
        require_keys(series, required={"seriesID", "data"}, allowed={"seriesID", "data"})
        series_id = series["seriesID"]
        if type(series_id) is not str or _SERIES_ID.fullmatch(series_id) is None:
            raise SourceSchemaDriftError("seriesID is not canonical")
        data = require_type(series["data"], list, "data")
        if type(data) is not list or len(data) > _MAX_OBSERVATIONS_PER_SERIES:
            raise SourceSchemaDriftError("series data must be a bounded array")
        for entry in data:
            if not isinstance(entry, dict):
                raise SourceSchemaDriftError("series data entries must be objects")
            require_keys(
                entry,
                required={"year", "period", "value"},
                allowed={"year", "period", "value", "footnotes"},
            )
            year = entry["year"]
            if type(year) is not str or _YEAR.fullmatch(year) is None:
                raise SourceSchemaDriftError("observation year must be YYYY")
            period = entry["period"]
            if type(period) is not str or _PERIOD.fullmatch(period) is None:
                raise SourceSchemaDriftError("observation period is unknown; fail closed")
            value = entry["value"]
            if type(value) is not str or not value:
                raise SourceSchemaDriftError("observation value must be text")
            month = _MONTH_OF_PERIOD.get(period, 12)
            observation_at = provider_utc_date(f"{year}-{month:02d}-01")
            records.append(
                build_normalized_record(
                    record_id=(f"bls-{series_id}-{year}-{period}-{content_hash[:12]}"),
                    family=P4SourceFamily.BLS,
                    endpoint_id="bls_series",
                    schema_version=schema_version("1.0.0"),
                    content_hash=content_hash,
                    retrieved_at=retrieved_at,
                    observation_at=observation_at,
                    payload=canonical_payload(
                        {"series_id": series_id, "year": year, "period": period, "value": value}
                    ),
                    material_claim=False,
                )
            )
    return tuple(records)
