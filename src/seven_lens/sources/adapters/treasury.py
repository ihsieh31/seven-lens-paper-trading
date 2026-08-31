"""Treasury FiscalData adapter: observation periods kept separate from release time."""

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

_RECORD_DATE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATASET: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_MAX_ROWS: Final = 10_000
_DATASET_FIELDS: Final = {
    "average-interest-rates": frozenset({"record_date", "security_type", "avg_interest_rate"}),
}


def parse_fiscal_data(
    payload: bytes, *, retrieved_at: UtcTimestamp, dataset: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one FiscalData dataset page; release time is never fabricated."""
    if type(dataset) is not str or _DATASET.fullmatch(dataset) is None:
        raise SourceSchemaDriftError("dataset must be a FiscalData slug")
    allowed_row_fields = _DATASET_FIELDS.get(dataset)
    if allowed_row_fields is None:
        raise SourceSchemaDriftError("dataset schema is not pinned for P4-A")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("fiscal data payload must be an object")
    require_keys(decoded, required={"data"}, allowed={"data", "meta", "links"})
    rows = require_type(decoded["data"], list, "data")
    if type(rows) is not list or not rows or len(rows) > _MAX_ROWS:
        raise SourceSchemaDriftError("data rows must be a non-empty bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SourceSchemaDriftError("data rows must be objects")
        require_keys(row, required={"record_date"}, allowed=set(allowed_row_fields))
        record_date = row["record_date"]
        if type(record_date) is not str or _RECORD_DATE.fullmatch(record_date) is None:
            raise SourceSchemaDriftError("record_date must use YYYY-MM-DD")
        normalized_row = {key: row[key] for key in sorted(row)}
        records.append(
            build_normalized_record(
                record_id=f"treasury-{dataset}-{record_date}-{content_hash[:12]}",
                family=P4SourceFamily.TREASURY,
                endpoint_id="fiscal_dataset",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=provider_utc_date(record_date),
                payload=canonical_payload({"dataset": dataset, **normalized_row}),
                material_claim=False,
            )
        )
    return tuple(records)
