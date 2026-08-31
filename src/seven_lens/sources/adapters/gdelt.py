"""GDELT adapter: DOC 2.0 article discovery with seen-time observation semantics."""

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
    parse_provider_timestamp,
    require_keys,
    require_type,
    schema_version,
    strict_json_loads,
)
from seven_lens.sources.adapters.records import (
    _build_normalized_record as build_normalized_record,
)
from seven_lens.sources.roles import P4SourceFamily

_SEEN_DATE: Final = re.compile(r"^\d{8}T\d{6}Z$")
_HTTPS_URL: Final = re.compile(r"^https://[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](/[^\s]*)?$")
_DOMAIN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,251}$")
_MAX_ARTICLES: Final = 250


def parse_doc_articles(
    payload: bytes, *, retrieved_at: UtcTimestamp, query: str
) -> tuple[NormalizedSourceRecord, ...]:
    """Validate one GDELT DOC response; articles are discovery-only records."""
    if type(query) is not str or not 1 <= len(query) <= 512:
        raise SourceSchemaDriftError("search query must be bounded text")
    decoded = strict_json_loads(payload)
    if not isinstance(decoded, dict):
        raise SourceSchemaDriftError("gdelt payload must be an object")
    require_keys(decoded, required={"articles"}, allowed={"articles"})
    articles = require_type(decoded["articles"], list, "articles")
    if type(articles) is not list or len(articles) > _MAX_ARTICLES:
        raise SourceSchemaDriftError("articles must be a bounded array")
    content_hash = content_hash_of(payload)
    records: list[NormalizedSourceRecord] = []
    for article in articles:
        if not isinstance(article, dict):
            raise SourceSchemaDriftError("article entries must be objects")
        require_keys(
            article,
            required={"url", "title", "seendate", "domain"},
            allowed={"url", "title", "seendate", "domain", "language", "sourcecountry"},
        )
        url = article["url"]
        if type(url) is not str or _HTTPS_URL.fullmatch(url) is None or len(url) > 2048:
            raise SourceSchemaDriftError("article url must be canonical HTTPS")
        title = article["title"]
        if type(title) is not str or not 1 <= len(title) <= 512:
            raise SourceSchemaDriftError("article title must be bounded text")
        seendate = article["seendate"]
        if type(seendate) is not str or _SEEN_DATE.fullmatch(seendate) is None:
            raise SourceSchemaDriftError("seendate must use the GDELT compact format")
        try:
            seen_at = parse_provider_timestamp(seendate)
        except ProviderTimestampError as error:
            raise SourceSchemaDriftError("seendate is not a valid provider timestamp") from error
        domain = article["domain"]
        if type(domain) is not str or _DOMAIN.fullmatch(domain) is None:
            raise SourceSchemaDriftError("article domain is not canonical")
        records.append(
            build_normalized_record(
                record_id=f"gdelt-discovery-{content_hash[:16]}-{len(records)}",
                family=P4SourceFamily.GDELT,
                endpoint_id="gdelt_doc",
                schema_version=schema_version("1.0.0"),
                content_hash=content_hash,
                retrieved_at=retrieved_at,
                observation_at=seen_at,
                payload=canonical_payload(
                    {
                        "query": query,
                        "url": url,
                        "title": title,
                        "domain": domain,
                        "seendate": seendate,
                    }
                ),
                material_claim=False,
            )
        )
    return tuple(records)
