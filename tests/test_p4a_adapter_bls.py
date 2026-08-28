# mypy: ignore-errors
"""P4-A BLS adapter tests: series payload status and period schema."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.bls import parse_series
from seven_lens.sources.adapters.records import SourceSchemaDriftError

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_BLS_JSON = b"""{
  "status": "REQUEST_SUCCEEDED",
  "responseTime": 21,
  "message": [],
  "Results": {
    "series": [
      {"seriesID": "CES0500000003",
       "data": [
         {"year": "2026", "period": "M07", "value": "30.66", "footnotes": [{}]},
         {"year": "2026", "period": "M06", "value": "30.60", "footnotes": [{}]}
       ]}
    ]
  }
}"""


def test_parse_bls_series_builds_records_with_period_observations() -> None:
    records = parse_series(_BLS_JSON, retrieved_at=_RETRIEVED)

    assert len(records) == 2
    first = records[0]
    assert first.payload.to_dict()["series_id"] == "CES0500000003"
    assert first.payload.to_dict()["period"] == "M07"
    assert str(first.observation_at) == "2026-07-01T00:00:00.000000Z"
    assert first.published_at is None


def test_parse_bls_series_rejects_failed_request_status() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_series(
            _BLS_JSON.replace(b'"REQUEST_SUCCEEDED"', b'"REQUEST_NOT_PROCESSED"'),
            retrieved_at=_RETRIEVED,
        )


def test_parse_bls_series_rejects_unknown_periods_or_values() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_series(_BLS_JSON.replace(b'"M06"', b'"X99"'), retrieved_at=_RETRIEVED)
    with pytest.raises(SourceSchemaDriftError):
        parse_series(
            _BLS_JSON.replace(b'"value": "30.60"', b'"value": 30.60'),
            retrieved_at=_RETRIEVED,
        )
