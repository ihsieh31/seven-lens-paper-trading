# mypy: ignore-errors
"""Job identity and lease/fencing value-object tests (no database required)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from seven_lens.domain.jobs import (
    JobInstance,
    JobSpec,
    JobStatus,
    LeaseDuration,
    LeaseGrant,
    validate_lease_owner,
)
from seven_lens.domain.value_objects import TradingDate, UtcTimestamp

TRADING_DATE = TradingDate(date(2026, 8, 14))
DATABASE_TIME = UtcTimestamp(datetime(2026, 8, 14, 13, 0, tzinfo=UTC))
LEASED_UNTIL = UtcTimestamp(datetime(2026, 8, 14, 13, 5, tzinfo=UTC))


def make_spec(**overrides: object) -> JobSpec:
    values: dict[str, object] = {
        "trading_date": TRADING_DATE,
        "job_type": "research",
        "window": "open",
    }
    values.update(overrides)
    return JobSpec(**values)  # type: ignore[arg-type]


def make_grant(**overrides: object) -> LeaseGrant:
    values: dict[str, object] = {
        "job_key": make_spec().job_key,
        "lease_owner": "worker-01",
        "leased_until": LEASED_UNTIL,
        "fencing_token": 1,
        "attempt_count": 1,
        "database_time": DATABASE_TIME,
    }
    values.update(overrides)
    return LeaseGrant(**values)  # type: ignore[arg-type]


def test_job_spec_has_deterministic_identity_and_key() -> None:
    spec = make_spec()

    assert spec.trading_date == TRADING_DATE
    assert spec.job_key == "2026-08-14/research/open"
    assert make_spec() == spec


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trading_date", date(2026, 8, 14)),
        ("job_type", ""),
        ("job_type", "Research"),
        ("job_type", "research window"),
        ("job_type", "x" * 65),
        ("window", ""),
        ("window", "OPEN"),
        ("window", "open/window"),
        ("window", "x" * 65),
    ],
)
def test_job_spec_rejects_invalid_or_ambiguous_identity_fields(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        make_spec(**{field: value})


@pytest.mark.parametrize(
    "duration",
    [timedelta(microseconds=1), timedelta(minutes=5), timedelta(days=1)],
)
def test_lease_duration_accepts_positive_boundary_durations(duration: timedelta) -> None:
    assert LeaseDuration(duration).value == duration


@pytest.mark.parametrize(
    "duration",
    [
        timedelta(0),
        timedelta(microseconds=-1),
        timedelta(days=-1),
        timedelta(days=1, microseconds=1),
    ],
)
def test_lease_duration_rejects_zero_negative_and_overlong_durations(duration: timedelta) -> None:
    with pytest.raises(ValueError):
        LeaseDuration(duration)


@pytest.mark.parametrize("owner", ["worker-01", "worker.01", "mac:user/x", "A" * 200])
def test_lease_owner_accepts_bounded_canonical_values(owner: str) -> None:
    assert validate_lease_owner(owner) == owner


@pytest.mark.parametrize(
    "owner",
    ["", " ", "worker 01", "worker+01", "worker?01", "使用者", "A" * 201, None, 1],
)
def test_lease_owner_rejects_invalid_values(owner: object) -> None:
    with pytest.raises(ValueError):
        validate_lease_owner(owner)


def test_lease_grant_accepts_normal_values_and_fencing_token_one() -> None:
    grant = make_grant()

    assert grant.job_key == "2026-08-14/research/open"
    assert grant.fencing_token == 1
    assert grant.attempt_count == 1
    assert grant.leased_until.value > grant.database_time.value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_key", ""),
        ("lease_owner", "bad owner"),
        ("leased_until", DATABASE_TIME),
        ("leased_until", UtcTimestamp(datetime(2026, 8, 14, 12, 59, tzinfo=UTC))),
        ("fencing_token", 0),
        ("fencing_token", -1),
        ("fencing_token", True),
        ("attempt_count", 0),
        ("attempt_count", -1),
        ("attempt_count", True),
        ("database_time", datetime(2026, 8, 14, 13, 0, tzinfo=UTC)),
    ],
)
def test_lease_grant_rejects_invalid_or_stale_fencing_values(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        make_grant(**{field: value})


def test_job_instance_exposes_spec_identity_and_status() -> None:
    instance = JobInstance(
        spec=make_spec(),
        status=JobStatus.PLANNED,
        lease_owner=None,
        leased_until=None,
        fencing_token=0,
        attempt_count=0,
        created_at=DATABASE_TIME,
        updated_at=DATABASE_TIME,
    )

    assert instance.job_key == make_spec().job_key
    assert instance.status is JobStatus.PLANNED
    assert tuple(JobStatus) == (
        JobStatus.PLANNED,
        JobStatus.RUNNING,
        JobStatus.COMPLETE,
        JobStatus.FAILED,
        JobStatus.EXPIRED,
    )
