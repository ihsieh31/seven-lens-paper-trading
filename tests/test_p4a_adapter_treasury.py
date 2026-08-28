# mypy: ignore-errors
"""P4-A Treasury FiscalData adapter tests: observation period vs release time."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import SourceSchemaDriftError
from seven_lens.sources.adapters.treasury import parse_fiscal_data

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_TREASURY_JSON = b"""{
  "data": [
    {"record_date": "2026-07-31", "security_type": "Marketable", "avg_interest_rate": "3.187"},
    {"record_date": "2026-06-30", "security_type": "Marketable", "avg_interest_rate": "3.152"}
  ],
  "meta": {"count": 2, "labels": {"record_date": "Record Date"}}
}"""


def test_parse_fiscal_data_separates_observation_period_from_release_time() -> None:
    records = parse_fiscal_data(
        _TREASURY_JSON, retrieved_at=_RETRIEVED, dataset="average-interest-rates"
    )

    assert len(records) == 2
    record = records[0]
    assert str(record.observation_at) == "2026-07-31T00:00:00.000000Z"
    assert record.published_at is None
    assert record.available_at is None
    assert record.payload.to_dict()["dataset"] == "average-interest-rates"
    assert record.record_id.startswith("treasury-average-interest-rates-")


def test_parse_fiscal_data_requires_record_date_on_every_row() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_fiscal_data(
            _TREASURY_JSON.replace(b'"record_date": "2026-06-30", ', b""),
            retrieved_at=_RETRIEVED,
            dataset="average-interest-rates",
        )


def test_parse_fiscal_data_rejects_malformed_dates_or_datasets() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_fiscal_data(
            _TREASURY_JSON.replace(b'"2026-07-31"', b'"07/31/2026"'),
            retrieved_at=_RETRIEVED,
            dataset="average-interest-rates",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_fiscal_data(_TREASURY_JSON, retrieved_at=_RETRIEVED, dataset="Bad_Dataset!")

    with pytest.raises(SourceSchemaDriftError):
        parse_fiscal_data(
            _TREASURY_JSON.replace(
                b'"avg_interest_rate": "3.152"',
                b'"avg_interest_rate": "3.152", "unexpected": "drift"',
            ),
            retrieved_at=_RETRIEVED,
            dataset="average-interest-rates",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_fiscal_data(_TREASURY_JSON, retrieved_at=_RETRIEVED, dataset="unknown-dataset")
