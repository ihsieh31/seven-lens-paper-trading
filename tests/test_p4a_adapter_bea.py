# mypy: ignore-errors
"""P4-A BEA adapter tests: envelope schema and error propagation."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.bea import parse_bea_data
from seven_lens.sources.adapters.records import SourceSchemaDriftError

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_BEA_JSON = b"""{
  "BEAAPI": {
    "Results": {
      "Statistic": "GDP in current dollars",
      "Data": [
        {"Year": "2026", "Period": "Q2", "DataValue": "30116.4", "SeriesCode": "A191RC"},
        {"Year": "2026", "Period": "Q1", "DataValue": "29976.1", "SeriesCode": "A191RC"}
      ]
    }
  }
}"""


def test_parse_bea_data_builds_records_from_nested_envelope() -> None:
    records = parse_bea_data(
        _BEA_JSON, retrieved_at=_RETRIEVED, dataset="NIPA", table_name="T10101"
    )

    assert len(records) == 2
    first = records[0]
    payload = first.payload.to_dict()
    assert payload["dataset"] == "NIPA"
    assert payload["table_name"] == "T10101"
    assert payload["period"] == "Q2"
    assert str(first.observation_at) == "2026-04-01T00:00:00.000000Z"


def test_parse_bea_data_propagates_error_envelopes() -> None:
    error_payload = b'{"BEAAPI": {"Error": [{"ErrorCode": "100", "ErrorMessage": "bad params"}]}}'
    with pytest.raises(SourceSchemaDriftError):
        parse_bea_data(error_payload, retrieved_at=_RETRIEVED, dataset="NIPA", table_name="T10101")


def test_parse_bea_data_rejects_unknown_periods_or_shapes() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_bea_data(
            _BEA_JSON.replace(b'"Q1"', b'"Q9"'),
            retrieved_at=_RETRIEVED,
            dataset="NIPA",
            table_name="T10101",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_bea_data(
            b'{"BEAAPI": {}}', retrieved_at=_RETRIEVED, dataset="NIPA", table_name="T10101"
        )
