"""FRED/ALFRED adapter: vintage-explicit macro observations; never defaults to today."""

from __future__ import annotations

import re
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    ProviderTimestampError,
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

_DATE_TEXT: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SERIES_ID: Final = re.compile(r"^[A-Z0-9][A-Z0-9_/]{0,31}$")
_MAX_OBSERVATIONS: Final = 10_000


class VintageSemanticsError(ValueError):
    """Raised when a historical macro request lacks an explicit realtime window."""


def _require_window(realtime_start: str | None, realtime_end: str | None) -> tuple[str, str]:
    if realtime_start is None or realtime_end is None:
        raise VintageSemanticsError(
            "historical macro requests require an explicit realtime vintage window"
        )
    if (
        type(realtime_start) is not str
        or type(realtime_end) is not str
        or not _DATE_TEXT.fullmatch(realtime_start)
        or not _DATE_TEXT.fullmatch(realtime_end)
    ):
        raise VintageSemanticsError("realtime window must use YYYY-MM-DD dates")
    try:
        provider_utc_date(realtime_start)
        provider_utc_date(realtime_end)
    except ProviderTimestampError as error:
        raise VintageSemanticsError("realtime window contains an invalid calendar date") from error
    if realtime_start > realtime_end:
        raise VintageSemanticsError("realtime window is inverted")
    return realtime_start, realtime_end


def parse_observations(
    payload: bytes,
    *,
    retrieved_at: UtcTimestamp,
    series_id: str,
    realtime_start: str | None = None,
    realtime_end: str | None = None,
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one FRED/ALFRED observation page bound to its explicit vintage."""
    vintage = _require_window(realtime_start, realtime_end)
    if type(series_id) is not str or _SERIES_ID.fullmatch(series_id) is None:
        raise SourceSchemaDriftError("series id is not canonical")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("observations payload must be an object")
    require_keys(
        decoded,
        required={"observations"},
        allowed={"observations", "count", "offset", "limit"},
    )
    observations = require_type(decoded["observations"], list, "observations")
    if type(observations) is not list or len(observations) > _MAX_OBSERVATIONS:
        raise SourceSchemaDriftError("observations must be a bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for observation in observations:
        if not isinstance(observation, dict):
            raise SourceSchemaDriftError("observation entries must be objects")
        require_keys(
            observation,
            required={"date", "value", "realtime_start", "realtime_end"},
            allowed={"date", "value", "realtime_start", "realtime_end"},
        )
        observation_date = observation["date"]
        if type(observation_date) is not str or not _DATE_TEXT.fullmatch(observation_date):
            raise SourceSchemaDriftError("observation date must use YYYY-MM-DD")
        value = observation["value"]
        if type(value) is not str or not value:
            raise SourceSchemaDriftError("observation value must be text (or the '.' miss)")
        for vintage_field in ("realtime_start", "realtime_end"):
            observed_vintage = observation[vintage_field]
            if type(observed_vintage) is not str or not _DATE_TEXT.fullmatch(observed_vintage):
                raise SourceSchemaDriftError("observation vintage dates must be YYYY-MM-DD")
            try:
                provider_utc_date(observed_vintage)
            except ProviderTimestampError as error:
                raise SourceSchemaDriftError(
                    "observation vintage date is not a valid calendar date"
                ) from error
        if observation["realtime_start"] > observation["realtime_end"]:
            raise SourceSchemaDriftError("observation vintage window is inverted")
        if (observation["realtime_start"], observation["realtime_end"]) != vintage:
            raise SourceSchemaDriftError("observation vintage does not match the requested window")
        records.append(
            build_normalized_record(
                record_id=(
                    f"fred-obs-{series_id}-{observation_date}-"
                    f"{observation['realtime_start']}-{observation['realtime_end']}"
                ),
                family=P4SourceFamily.FRED_ALFRED,
                endpoint_id="fred_observations",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=provider_utc_date(observation_date),
                vintage=vintage,
                payload=canonical_payload(
                    {
                        "series_id": series_id,
                        "date": observation_date,
                        "value": value,
                        "realtime_start": observation["realtime_start"],
                        "realtime_end": observation["realtime_end"],
                    }
                ),
                material_claim=False,
            )
        )
    return tuple(records)
