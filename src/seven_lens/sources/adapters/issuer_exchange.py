"""Issuer IR and exchange official adapters for confirmation-grade notices."""

from __future__ import annotations

import re
from typing import Final

from seven_lens.domain.value_objects import UtcTimestamp
from seven_lens.sources.adapters.records import (
    NormalizedSourceRecord,
    ProviderTimestampError,
    SourceSchemaDriftError,
    build_normalized_record,
    canonical_payload,
    content_hash_of,
    parse_provider_timestamp,
    require_date,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.roles import P4SourceFamily

_CANONICAL_HTTPS_URL: Final = re.compile(
    r"^https://[a-z0-9][a-z0-9.-]*[a-z0-9](?::443)?(/[^\s]*)?$"
)
_ISSUER_ID: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_NOTICE_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_FULL_STAMP: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")
_REGISTERED_EXCHANGES: Final = frozenset({"NYSE", "NASDAQ"})
_MAX_NOTICES: Final = 500


def _require_https(url: object) -> str:
    if type(url) is not str or _CANONICAL_HTTPS_URL.fullmatch(url) is None:
        raise SourceSchemaDriftError("notice url must be canonical HTTPS")
    return url


def parse_issuer_press(
    payload: bytes, *, retrieved_at: UtcTimestamp, issuer_id: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one issuer IR press-release page as metadata-only confirmation input."""
    if type(issuer_id) is not str or _ISSUER_ID.fullmatch(issuer_id) is None:
        raise SourceSchemaDriftError("issuer id must be sanitized text")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("issuer press payload must be an object")
    require_keys(decoded, required={"press_releases"}, allowed={"press_releases"})
    releases = require_type(decoded["press_releases"], list, "press_releases")
    if type(releases) is not list or len(releases) > _MAX_NOTICES:
        raise SourceSchemaDriftError("press_releases must be a bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for release in releases:
        if not isinstance(release, dict):
            raise SourceSchemaDriftError("press release entries must be objects")
        require_keys(
            release,
            required={"id", "title", "url", "published_at"},
            allowed={"id", "title", "url", "published_at"},
        )
        release_id = release["id"]
        if type(release_id) is not str or _NOTICE_ID.fullmatch(release_id) is None:
            raise SourceSchemaDriftError("press release id is not canonical")
        title = require_type(release["title"], str, "title")
        if type(title) is not str or not 1 <= len(title) <= 512:
            raise SourceSchemaDriftError("press release title must be bounded text")
        url = _require_https(release["url"])
        published_at = require_date_checked(release["published_at"])
        records.append(
            build_normalized_record(
                record_id=f"issuer-ir-{issuer_id}-{release_id}",
                family=P4SourceFamily.ISSUER_IR,
                endpoint_id="issuer_press",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                published_at=published_at,
                payload=canonical_payload({"issuer_id": issuer_id, "title": title, "url": url}),
                material_claim=False,
            )
        )
    return tuple(records)


def parse_exchange_notice(
    payload: bytes, *, retrieved_at: UtcTimestamp
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one exchange notice page from a registered listing exchange."""
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("exchange notice payload must be an object")
    require_keys(decoded, required={"notices"}, allowed={"notices"})
    notices = require_type(decoded["notices"], list, "notices")
    if type(notices) is not list or len(notices) > _MAX_NOTICES:
        raise SourceSchemaDriftError("notices must be a bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for notice in notices:
        if not isinstance(notice, dict):
            raise SourceSchemaDriftError("notice entries must be objects")
        require_keys(
            notice,
            required={"id", "title", "url", "exchange", "published_at"},
            allowed={"id", "title", "url", "exchange", "published_at"},
        )
        notice_id = notice["id"]
        if type(notice_id) is not str or _NOTICE_ID.fullmatch(notice_id) is None:
            raise SourceSchemaDriftError("notice id is not canonical")
        exchange = notice["exchange"]
        if exchange not in _REGISTERED_EXCHANGES:
            raise SourceSchemaDriftError("exchange is not a registered listing exchange")
        title = require_type(notice["title"], str, "title")
        if type(title) is not str or not 1 <= len(title) <= 512:
            raise SourceSchemaDriftError("notice title must be bounded text")
        url = _require_https(notice["url"])
        published_at = require_date_checked(notice["published_at"])
        records.append(
            build_normalized_record(
                record_id=f"exchange-notice-{exchange}-{notice_id}",
                family=P4SourceFamily.EXCHANGE_OFFICIAL,
                endpoint_id="exchange_notice",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                published_at=published_at,
                payload=canonical_payload({"exchange": exchange, "title": title, "url": url}),
                material_claim=False,
            )
        )
    return tuple(records)


def require_date_checked(value: object) -> UtcTimestamp:
    """Accept either a full provider timestamp or a date-only publish stamp."""
    if type(value) is str and _FULL_STAMP.fullmatch(value) is not None:
        return parse_provider_timestamp_checked(value)
    return require_date(value, "published_at")


def parse_provider_timestamp_checked(text: str) -> UtcTimestamp:
    try:
        return parse_provider_timestamp(text)
    except ProviderTimestampError as error:
        raise SourceSchemaDriftError("published_at is not a bounded provider stamp") from error
