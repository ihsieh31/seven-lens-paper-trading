"""Contract tests for the strict domain value objects.

These tests intentionally exercise the boundary between a value object and
its wire representation.  The trading domain stores timestamps in UTC and
does not silently coerce ambiguous input.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from seven_lens.domain.value_objects import (
    RunId,
    SchemaVersion,
    TradingDate,
    UtcTimestamp,
)


def test_run_id_accepts_canonical_uuid_and_new_ids_are_non_nil() -> None:
    value = "123e4567-e89b-12d3-a456-426614174000"

    run_id = RunId.from_string(value)

    assert run_id.value == UUID(value)
    assert str(run_id) == value
    generated = RunId.new()
    assert generated.value.int != 0
    assert RunId.from_string(str(generated)) == generated


@pytest.mark.parametrize(
    "bad_value",
    [
        UUID(int=0),
        "00000000-0000-0000-0000-000000000000",
        "123E4567-E89B-12D3-A456-426614174000",
        "{123e4567-e89b-12d3-a456-426614174000}",
        "123e4567e89b12d3a456426614174000",
        "not-a-uuid",
        None,
        42,
    ],
)
def test_run_id_rejects_nil_noncanonical_and_non_uuid_input(bad_value: object) -> None:
    if isinstance(bad_value, UUID):
        with pytest.raises(ValueError):
            RunId(bad_value)
    else:
        with pytest.raises(ValueError):
            RunId.from_string(bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        date.min,
        date(2024, 2, 29),
        date(9999, 12, 31),
    ],
)
def test_trading_date_accepts_calendar_boundaries(value: date) -> None:
    trading_date = TradingDate(value)

    assert trading_date.value == value
    assert str(trading_date) == value.isoformat()
    assert TradingDate.from_isoformat(value.isoformat()) == trading_date


@pytest.mark.parametrize(
    "bad_value",
    [
        datetime(2024, 1, 2),
        "2024-2-2",
        "2024-02-30",
        "2024-01-02T00:00:00",
        "2024/01/02",
        None,
    ],
)
def test_trading_date_rejects_datetime_and_malformed_iso_dates(bad_value: object) -> None:
    with pytest.raises(ValueError):
        if isinstance(bad_value, date) and not isinstance(bad_value, datetime):
            TradingDate(bad_value)
        else:
            TradingDate.from_isoformat(bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, 3, 4, 5, 999999, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
        # A zero-offset timezone is still UTC for storage semantics.
        datetime(2024, 1, 2, tzinfo=timezone(timedelta(0), name="UTC+00")),
    ],
)
def test_utc_timestamp_accepts_aware_zero_offset_datetimes(value: datetime) -> None:
    timestamp = UtcTimestamp(value)

    assert timestamp.value == value
    assert timestamp.value.utcoffset() == timedelta(0)
    assert UtcTimestamp.from_isoformat(str(timestamp)) == timestamp


def test_utc_timestamp_now_is_aware_and_utc() -> None:
    timestamp = UtcTimestamp.now()

    assert timestamp.value.tzinfo is not None
    assert timestamp.value.utcoffset() == timedelta(0)


def test_utc_timestamp_wire_parser_accepts_only_fixed_canonical_utc() -> None:
    wire_value = "2024-01-02T03:04:05.123456Z"

    timestamp = UtcTimestamp.from_isoformat(wire_value)

    assert timestamp.value == datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    assert str(timestamp) == wire_value


@pytest.mark.parametrize(
    "bad_value",
    [
        datetime(2024, 1, 2),
        datetime(2024, 1, 2, tzinfo=timezone(timedelta(hours=8))),
        "2024-01-02T03:04:05",
        "2024-01-02T03:04:05+08:00",
        "2024-01-02T03:04:05.000000+00:00",
        "2024-01-02T03:04:05.000000-00:00",
        "2024-01-02x03:04:05.000000Z",
        "2024-W01-2T03:04:05.000000Z",
        "20240102T030405.000000Z",
        "2024-01-02T03:04:05Z",
        "2024-01-02T03:04:05.000000z",
        "not-a-timestamp",
        None,
        1704157440,
    ],
)
def test_utc_timestamp_rejects_naive_non_utc_and_non_datetime_input(
    bad_value: object,
) -> None:
    with pytest.raises(ValueError):
        if isinstance(bad_value, datetime):
            UtcTimestamp(bad_value)
        else:
            UtcTimestamp.from_isoformat(bad_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["0.0.0", "1.2.3", "10.0.42", "9999.9999.9999"])
def test_schema_version_accepts_semver_like_numeric_triplets(value: str) -> None:
    version = SchemaVersion(value)

    major, minor, patch = (int(part) for part in value.split("."))
    assert (version.major, version.minor, version.patch) == (major, minor, patch)
    assert str(version) == value


@pytest.mark.parametrize(
    "bad_value",
    [
        "",
        "1",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "-1.2.3",
        "1.2.-3",
        "1.2.3-alpha",
        " 1.2.3",
        "1.2.3 ",
        "10000.0.0",
        "0.10000.0",
        "0.0.10000",
        f"{'9' * 10_000}.1.0",
        None,
        123,
    ],
)
def test_schema_version_rejects_ambiguous_or_non_string_versions(bad_value: object) -> None:
    with pytest.raises(ValueError):
        SchemaVersion(bad_value)  # type: ignore[arg-type]
