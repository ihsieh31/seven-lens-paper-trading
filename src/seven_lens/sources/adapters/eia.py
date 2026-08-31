"""EIA adapter: v2 series routes with strict period and response schemas."""

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

_ROUTE: Final = re.compile(r"^[a-z0-9][a-z0-9/-]{0,118}$")
_MONTHLY: Final = re.compile(r"^\d{4}-\d{2}$")
_YEARLY: Final = re.compile(r"^\d{4}$")
_WEEKLY: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DAILY: Final = _WEEKLY
_MAX_ROWS: Final = 10_000
_ROW_FIELDS: Final = frozenset(
    {
        "period",
        "value",
        "units",
        "unit",
        "duoarea",
        "area-name",
        "product",
        "product-name",
        "process",
        "process-name",
        "seriesId",
        "seriesDescription",
        "stateId",
        "stateDescription",
        "fuelId",
        "fuelName",
        "sectorId",
        "sectorName",
    }
)


def parse_series_data(
    payload: bytes, *, retrieved_at: UtcTimestamp, route: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one EIA v2 route response; error payloads fail closed."""
    if type(route) is not str or _ROUTE.fullmatch(route) is None:
        raise SourceSchemaDriftError("eia route must be a lowercase series route")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("eia payload must be an object")
    if "error" in decoded:
        raise SourceSchemaDriftError("eia response carried an error payload")
    require_keys(decoded, required={"response"}, allowed={"response", "apiVersion", "requestId"})
    response = require_type(decoded["response"], dict, "response")
    if type(response) is not dict:
        raise SourceSchemaDriftError("response must be an object")
    require_keys(response, required={"data"}, allowed={"data", "total", "dateFormat", "frequency"})
    data = require_type(response["data"], list, "data")
    if type(data) is not list or not data or len(data) > _MAX_ROWS:
        raise SourceSchemaDriftError("data must be a non-empty bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for row in data:
        if not isinstance(row, dict):
            raise SourceSchemaDriftError("data rows must be objects")
        require_keys(row, required={"period", "value"}, allowed=set(_ROW_FIELDS))
        period = row["period"]
        if type(period) is not str or not (
            _MONTHLY.fullmatch(period)
            or _YEARLY.fullmatch(period)
            or _WEEKLY.fullmatch(period)
            or _DAILY.fullmatch(period)
        ):
            raise SourceSchemaDriftError("period must be a bounded EIA period format")
        value = row["value"]
        if type(value) is not str or not value:
            raise SourceSchemaDriftError("value must be text")
        observation_date = (
            period
            if _DAILY.fullmatch(period)
            else (f"{period}-01" if _MONTHLY.fullmatch(period) else f"{period}-01-01")
        )
        observation_at = provider_utc_date(observation_date)
        normalized_row = {key: row[key] for key in sorted(row)}
        records.append(
            build_normalized_record(
                record_id=f"eia-{route.replace('/', '_')}-{period}-{content_hash[:12]}",
                family=P4SourceFamily.EIA,
                endpoint_id="eia_route",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=observation_at,
                payload=canonical_payload({"route": route, **normalized_row}),
                material_claim=False,
            )
        )
    return tuple(records)
