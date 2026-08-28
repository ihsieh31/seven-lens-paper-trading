# mypy: ignore-errors
"""P4-A EIA adapter tests: route-bound series data."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.eia import parse_series_data
from seven_lens.sources.adapters.records import SourceSchemaDriftError

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_EIA_JSON = b"""{
  "response": {
    "total": 2,
    "data": [
      {"period": "2026-06", "value": "4.15", "units": "dollars per gallon"},
      {"period": "2026-05", "value": "4.06", "units": "dollars per gallon"}
    ]
  },
  "apiVersion": "2.0.0"
}"""


def test_parse_eia_series_builds_records_with_period_observations() -> None:
    records = parse_series_data(
        _EIA_JSON, retrieved_at=_RETRIEVED, route="petroleum/pri/gas/weekly"
    )

    assert len(records) == 2
    first = records[0]
    payload = first.payload.to_dict()
    assert payload["route"] == "petroleum/pri/gas/weekly"
    assert payload["period"] == "2026-06"
    assert str(first.observation_at) == "2026-06-01T00:00:00.000000Z"
    assert first.published_at is None


def test_parse_eia_series_rejects_error_responses() -> None:
    error_payload = b'{"error": "invalid api_key"}'
    with pytest.raises(SourceSchemaDriftError):
        parse_series_data(error_payload, retrieved_at=_RETRIEVED, route="petroleum/pri/gas/weekly")


def test_parse_eia_series_rejects_bad_periods_or_route() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_series_data(
            _EIA_JSON.replace(b'"2026-05"', b'"June 2026"'),
            retrieved_at=_RETRIEVED,
            route="petroleum/pri/gas/weekly",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_series_data(_EIA_JSON, retrieved_at=_RETRIEVED, route="Not A Route")


def test_parse_eia_series_rejects_unknown_row_fields() -> None:
    drifted = _EIA_JSON.replace(
        b'"units": "dollars per gallon"',
        b'"units": "dollars per gallon", "unexpected": "drift"',
    )
    with pytest.raises(SourceSchemaDriftError):
        parse_series_data(drifted, retrieved_at=_RETRIEVED, route="petroleum/pri/gas/weekly")
