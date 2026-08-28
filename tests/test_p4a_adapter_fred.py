# mypy: ignore-errors
"""P4-A FRED/ALFRED adapter tests: explicit vintage windows and observation schema."""

from __future__ import annotations

import pytest

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.fred import VintageSemanticsError, parse_observations
from seven_lens.sources.adapters.records import SourceSchemaDriftError

_RETRIEVED = UtcTimestamp.from_isoformat("2026-08-27T15:30:00.000000Z")

_OBS_JSON = b"""{"observations": [
    {"date": "2026-06-01", "value": "4.08",
     "realtime_start": "2026-08-01", "realtime_end": "2026-08-01"},
    {"date": "2026-07-01", "value": ".",
     "realtime_start": "2026-08-01", "realtime_end": "2026-08-01"}
  ], "count": 2}"""


def test_parse_observations_preserves_explicit_vintage_window() -> None:
    records = parse_observations(
        _OBS_JSON,
        retrieved_at=_RETRIEVED,
        series_id="DFF",
        realtime_start="2026-08-01",
        realtime_end="2026-08-01",
    )

    assert len(records) == 2
    record = records[0]
    assert record.vintage == ("2026-08-01", "2026-08-01")
    assert record.payload.to_dict()["realtime_start"] == "2026-08-01"
    assert str(record.observation_at) == "2026-06-01T00:00:00.000000Z"
    assert records[1].payload.to_dict()["value"] == "."


def test_parse_observations_rejects_missing_realtime_window() -> None:
    for kwargs in (
        {"realtime_start": "2026-08-01"},
        {"realtime_end": "2026-08-01"},
        {},
    ):
        with pytest.raises(VintageSemanticsError):
            parse_observations(_OBS_JSON, retrieved_at=_RETRIEVED, series_id="DFF", **kwargs)


def test_parse_observations_rejects_inverted_or_malformed_windows() -> None:
    with pytest.raises(VintageSemanticsError):
        parse_observations(
            _OBS_JSON,
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-08-10",
            realtime_end="2026-08-01",
        )
    with pytest.raises(VintageSemanticsError):
        parse_observations(
            _OBS_JSON,
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-08-xx",
            realtime_end="2026-08-01",
        )


def test_parse_observations_rejects_schema_drift() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_observations(
            _OBS_JSON.replace(b'"value": "4.08"', b'"value": 4.08'),
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-08-01",
            realtime_end="2026-08-01",
        )

    with pytest.raises(SourceSchemaDriftError):
        parse_observations(
            _OBS_JSON.replace(b'"2026-08-01", "realtime_end"', b'"2026-08-02", "realtime_end"'),
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-08-01",
            realtime_end="2026-08-01",
        )


def test_parse_observations_rejects_invalid_calendar_vintage() -> None:
    with pytest.raises(VintageSemanticsError):
        parse_observations(
            _OBS_JSON,
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-02-30",
            realtime_end="2026-03-01",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_observations(
            b'{"observations": "all"}',
            retrieved_at=_RETRIEVED,
            series_id="DFF",
            realtime_start="2026-08-01",
            realtime_end="2026-08-01",
        )


def test_parse_observations_rejects_noncanonical_series_ids() -> None:
    with pytest.raises(SourceSchemaDriftError):
        parse_observations(
            _OBS_JSON,
            retrieved_at=_RETRIEVED,
            series_id="lowercase",
            realtime_start="2026-08-01",
            realtime_end="2026-08-01",
        )
    with pytest.raises(SourceSchemaDriftError):
        parse_observations(
            _OBS_JSON,
            retrieved_at=_RETRIEVED,
            series_id="",
            realtime_start="2026-08-01",
            realtime_end="2026-08-01",
        )
